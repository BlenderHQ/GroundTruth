from __future__ import annotations

import argparse
import csv
import math
import shutil
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PoseSample:
    name: str
    path: Path
    position: tuple[float, float, float]
    rotation_rows: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]

    @property
    def inward_direction(self) -> tuple[float, float, float]:
        return normalize(self.rotation_rows[2])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Match source RealityCapture XMP poses to destination XMP names and copy the source "
            "files under the destination filenames."
        )
    )
    parser.add_argument("--src", type=Path, required=True, help="Directory with ground-truth XMP files.")
    parser.add_argument("--dst", type=Path, required=True, help="Directory with shuffled destination XMP files.")
    parser.add_argument("--out", type=Path, required=True, help="Output directory for renamed source XMP files.")
    parser.add_argument(
        "--min-cosine",
        type=float,
        default=0.995,
        help="Fail if any assigned source/destination direction cosine falls below this threshold.",
    )
    return parser.parse_args()


def parse_xmp_pose(path: Path) -> PoseSample:
    root = ET.parse(path).getroot()
    description = None
    rotation_text = None
    position_text = None

    for element in root.iter():
        if element.tag.endswith("Description") and description is None:
            description = element
        if element.tag.endswith("Rotation"):
            rotation_text = (element.text or "").strip()
        if element.tag.endswith("Position"):
            position_text = (element.text or "").strip()

    if description is not None:
        for key, value in description.attrib.items():
            if key.endswith("Rotation") and not rotation_text:
                rotation_text = value.strip()
            if key.endswith("Position") and not position_text:
                position_text = value.strip()

    if not rotation_text or not position_text:
        raise ValueError(f"Missing Rotation/Position in {path}")

    rotation_values = [float(part) for part in rotation_text.split()]
    position_values = [float(part) for part in position_text.split()]
    if len(rotation_values) != 9:
        raise ValueError(f"Expected 9 rotation values in {path}, got {len(rotation_values)}")
    if len(position_values) != 3:
        raise ValueError(f"Expected 3 position values in {path}, got {len(position_values)}")

    rows = (
        (rotation_values[0], rotation_values[1], rotation_values[2]),
        (rotation_values[3], rotation_values[4], rotation_values[5]),
        (rotation_values[6], rotation_values[7], rotation_values[8]),
    )
    position = (position_values[0], position_values[1], position_values[2])
    return PoseSample(name=path.stem, path=path, position=position, rotation_rows=rows)


def load_pose_dir(path: Path) -> list[PoseSample]:
    poses = [parse_xmp_pose(item) for item in sorted(path.glob("*.xmp"))]
    if not poses:
        raise ValueError(f"No XMP files found in {path}")
    return poses


def normalize(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(dot(vector, vector))
    if length == 0.0:
        raise ValueError("Cannot normalize a zero-length vector")
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def dot(lhs: tuple[float, float, float], rhs: tuple[float, float, float]) -> float:
    return lhs[0] * rhs[0] + lhs[1] * rhs[1] + lhs[2] * rhs[2]


def subtract(lhs: tuple[float, float, float], rhs: tuple[float, float, float]) -> tuple[float, float, float]:
    return (lhs[0] - rhs[0], lhs[1] - rhs[1], lhs[2] - rhs[2])


def add(lhs: tuple[float, float, float], rhs: tuple[float, float, float]) -> tuple[float, float, float]:
    return (lhs[0] + rhs[0], lhs[1] + rhs[1], lhs[2] + rhs[2])


def scale(vector: tuple[float, float, float], factor: float) -> tuple[float, float, float]:
    return (vector[0] * factor, vector[1] * factor, vector[2] * factor)


def solve_3x3(matrix: list[list[float]], rhs: list[float]) -> tuple[float, float, float]:
    augmented = [row[:] + [rhs_value] for row, rhs_value in zip(matrix, rhs)]

    for pivot_index in range(3):
        pivot_row = max(range(pivot_index, 3), key=lambda row_index: abs(augmented[row_index][pivot_index]))
        pivot_value = augmented[pivot_row][pivot_index]
        if abs(pivot_value) < 1e-12:
            raise ValueError("Singular 3x3 system while estimating sphere center")
        if pivot_row != pivot_index:
            augmented[pivot_index], augmented[pivot_row] = augmented[pivot_row], augmented[pivot_index]

        pivot_value = augmented[pivot_index][pivot_index]
        for column_index in range(pivot_index, 4):
            augmented[pivot_index][column_index] /= pivot_value

        for row_index in range(3):
            if row_index == pivot_index:
                continue
            factor = augmented[row_index][pivot_index]
            if factor == 0.0:
                continue
            for column_index in range(pivot_index, 4):
                augmented[row_index][column_index] -= factor * augmented[pivot_index][column_index]

    return (augmented[0][3], augmented[1][3], augmented[2][3])


def estimate_center_from_rays(poses: list[PoseSample]) -> tuple[float, float, float]:
    matrix = [[0.0, 0.0, 0.0] for _ in range(3)]
    rhs = [0.0, 0.0, 0.0]

    for pose in poses:
        direction = pose.inward_direction
        projector = [
            [1.0 - direction[0] * direction[0], -direction[0] * direction[1], -direction[0] * direction[2]],
            [-direction[1] * direction[0], 1.0 - direction[1] * direction[1], -direction[1] * direction[2]],
            [-direction[2] * direction[0], -direction[2] * direction[1], 1.0 - direction[2] * direction[2]],
        ]
        for row_index in range(3):
            rhs[row_index] += (
                projector[row_index][0] * pose.position[0]
                + projector[row_index][1] * pose.position[1]
                + projector[row_index][2] * pose.position[2]
            )
            for column_index in range(3):
                matrix[row_index][column_index] += projector[row_index][column_index]

    return solve_3x3(matrix, rhs)


def pose_descriptor(pose: PoseSample, center: tuple[float, float, float]) -> tuple[float, float, float]:
    position_direction = normalize(subtract(pose.position, center))
    outward_from_rotation = scale(pose.inward_direction, -1.0)
    blended = add(position_direction, outward_from_rotation)
    return normalize(blended)


def find_unique_best_match(
    src_descriptors: list[tuple[float, float, float]],
    dst_descriptors: list[tuple[float, float, float]],
    *,
    min_cosine: float,
) -> list[tuple[int, int, float]]:
    assignments: list[tuple[int, int, float]] = []
    used_src_indices: set[int] = set()

    for dst_index, dst_descriptor in enumerate(dst_descriptors):
        scored = sorted(
            (
                (dot(dst_descriptor, src_descriptor), src_index)
                for src_index, src_descriptor in enumerate(src_descriptors)
            ),
            reverse=True,
        )
        best_score, best_src_index = scored[0]
        if best_score < min_cosine:
            raise ValueError(
                f"Low-confidence match for destination index {dst_index}: best cosine {best_score:.6f} < {min_cosine:.6f}"
            )
        if best_src_index in used_src_indices:
            raise ValueError("Destination matching produced a duplicate source assignment")
        used_src_indices.add(best_src_index)
        assignments.append((dst_index, best_src_index, best_score))

    reciprocal_best: list[int] = []
    for src_descriptor in src_descriptors:
        best_score = -2.0
        best_dst_index = -1
        for dst_index, dst_descriptor in enumerate(dst_descriptors):
            score = dot(src_descriptor, dst_descriptor)
            if score > best_score:
                best_score = score
                best_dst_index = dst_index
        reciprocal_best.append(best_dst_index)

    for dst_index, src_index, score in assignments:
        if reciprocal_best[src_index] != dst_index:
            raise ValueError(
                f"Non-reciprocal match for destination index {dst_index} -> source index {src_index} (cosine {score:.6f})"
            )

    return assignments


def write_outputs(
    *,
    src_poses: list[PoseSample],
    dst_poses: list[PoseSample],
    assignments: list[tuple[int, int, float]],
    out_dir: Path,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    mapping_rows: list[tuple[str, str, float]] = []
    for dst_index, src_index, score in assignments:
        dst_pose = dst_poses[dst_index]
        src_pose = src_poses[src_index]
        output_path = out_dir / dst_pose.path.name
        shutil.copy2(src_pose.path, output_path)
        mapping_rows.append((dst_pose.name, src_pose.name, score))

    mapping_rows.sort(key=lambda row: row[0])
    csv_path = out_dir / "mapping.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["dst_name", "src_name", "direction_cosine"])
        for row in mapping_rows:
            writer.writerow([row[0], row[1], f"{row[2]:.12f}"])


def main() -> int:
    args = parse_args()
    src_poses = load_pose_dir(args.src)
    dst_poses = load_pose_dir(args.dst)
    if len(dst_poses) > len(src_poses):
        raise ValueError("Destination pose count exceeds source pose count")

    src_center = estimate_center_from_rays(src_poses)
    dst_center = estimate_center_from_rays(dst_poses)
    src_descriptors = [pose_descriptor(pose, src_center) for pose in src_poses]
    dst_descriptors = [pose_descriptor(pose, dst_center) for pose in dst_poses]
    assignments = find_unique_best_match(src_descriptors, dst_descriptors, min_cosine=args.min_cosine)
    write_outputs(src_poses=src_poses, dst_poses=dst_poses, assignments=assignments, out_dir=args.out)

    matched_src_names = {src_poses[src_index].name for _, src_index, _ in assignments}
    unmatched_src_names = sorted(pose.name for pose in src_poses if pose.name not in matched_src_names)
    scores = [score for _, _, score in assignments]

    print(f"Matched {len(assignments)} destination XMP files to {len(matched_src_names)} source XMP files.")
    print(f"Direction cosine range: min={min(scores):.12f} mean={sum(scores) / len(scores):.12f} max={max(scores):.12f}")
    if unmatched_src_names:
        print("Unmatched source XMP names:", ", ".join(unmatched_src_names))
    print(f"Renamed XMP files written to: {args.out}")
    print(f"Mapping report written to: {args.out / 'mapping.csv'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise
