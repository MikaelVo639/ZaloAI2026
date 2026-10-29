import hashlib
import json
import random
import shutil
import stat
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from datamate.io import contained_path, load_split


def import_archive(archive: Path, destination: Path) -> dict:
    """Preserve organizer layout; reject overwrites and paths outside destination."""
    destination = destination.resolve()
    with ZipFile(archive) as bundle:
        targets = []
        for item in bundle.infolist():
            relative = PurePosixPath(item.filename.replace("\\", "/"))
            if relative.is_absolute() or ".." in relative.parts or any(":" in p for p in relative.parts):
                raise ValueError(f"Invalid archive path: {item.filename}")
            if stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError(f"Archive symlinks are unsupported: {item.filename}")
            target = contained_path(destination, str(relative))
            if target.exists() and not item.is_dir():
                raise ValueError(f"Refusing to overwrite {target}")
            targets.append((item, target))
        for item, target in targets:
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(item) as source, target.open("xb") as sink:
                    shutil.copyfileobj(source, sink)
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"archive": str(archive.resolve()), "sha256": digest,
            "destination": str(destination), "entries": len(targets)}


def business_holdout(data_dir: Path, output: Path, fraction: float = 0.2, seed: int = 42) -> dict:
    if not 0 < fraction < 1:
        raise ValueError("Validation fraction must be between 0 and 1")
    split = load_split(data_dir)
    businesses = sorted({q.business_id for q in split.questions})
    if len(businesses) < 2:
        raise ValueError("At least two businesses are needed for a held-out split")
    random.Random(seed).shuffle(businesses)
    size = max(1, min(len(businesses) - 1, round(len(businesses) * fraction)))
    validation = set(businesses[:size])
    result = {"seed": seed, "source": str(split.root),
              "train_businesses": sorted(set(businesses) - validation),
              "validation_businesses": sorted(validation),
              "train_ids": [q.id for q in split.questions if q.business_id not in validation],
              "validation_ids": [q.id for q in split.questions if q.business_id in validation]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
