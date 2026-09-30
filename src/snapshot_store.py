"""Versioned, lossless JSON snapshots for deterministic CreditResult objects."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Type

import numpy as np
import pandas as pd

if __package__:
	from .models import CreditResult, ExceptionRecord, FinanceRecord, MatchResult, SLARecord
else:
	from models import CreditResult, ExceptionRecord, FinanceRecord, MatchResult, SLARecord


SNAPSHOT_FORMAT = "credit-report-agent/credit-results"
SNAPSHOT_SCHEMA_VERSION = 1
_DATACLASS_TYPES: Dict[str, Type[Any]] = {
	"CreditResult": CreditResult,
	"ExceptionRecord": ExceptionRecord,
	"FinanceRecord": FinanceRecord,
	"MatchResult": MatchResult,
	"SLARecord": SLARecord,
}


class SnapshotError(ValueError):
	"""Raised when a deterministic result snapshot is invalid or unsupported."""


def _encode(value: Any, active: Optional[set] = None) -> Any:
	if active is None:
		active = set()
	if value is None or isinstance(value, (str, bool, int)):
		return value
	if isinstance(value, Decimal):
		return {"$type": "decimal", "value": str(value)}
	if isinstance(value, np.generic):
		if value.dtype.hasobject:
			raise SnapshotError("NumPy object scalars are not supported in snapshots.")
		return {
			"$type": "numpy_scalar",
			"dtype": value.dtype.str,
			"value": _encode(value.item(), active),
		}
	if isinstance(value, float):
		return {"$type": "float", "hex": value.hex()}
	if value is pd.NA:
		return {"$type": "pandas.NA"}
	if value is pd.NaT:
		return {"$type": "pandas.NaT"}
	if isinstance(value, datetime):
		return {"$type": "datetime", "value": value.isoformat()}
	if isinstance(value, date):
		return {"$type": "date", "value": value.isoformat()}
	if isinstance(value, time):
		return {"$type": "time", "value": value.isoformat()}
	if is_dataclass(value) and not isinstance(value, type):
		class_name = type(value).__name__
		if class_name not in _DATACLASS_TYPES or type(value) is not _DATACLASS_TYPES[class_name]:
			raise SnapshotError("Unsupported dataclass type: {0}".format(class_name))
		object_id = id(value)
		if object_id in active:
			raise SnapshotError("Cyclic dataclass references cannot be snapshotted.")
		active.add(object_id)
		try:
			encoded_fields = {
				item.name: _encode(getattr(value, item.name), active)
				for item in fields(value)
			}
		finally:
			active.remove(object_id)
		return {"$type": "dataclass", "name": class_name, "fields": encoded_fields}
	if isinstance(value, tuple):
		return {"$type": "tuple", "items": [_encode(item, active) for item in value]}
	if isinstance(value, list):
		return [_encode(item, active) for item in value]
	if isinstance(value, dict):
		return {
			"$type": "dict",
			"items": [[_encode(key, active), _encode(item, active)] for key, item in value.items()],
		}
	raise SnapshotError("Unsupported snapshot value type: {0}".format(type(value).__name__))


def _decode(value: Any) -> Any:
	if isinstance(value, list):
		return [_decode(item) for item in value]
	if not isinstance(value, dict):
		return value

	tag = value.get("$type")
	if tag is None:
		return {key: _decode(item) for key, item in value.items()}
	if tag == "decimal":
		try:
			return Decimal(value["value"])
		except Exception as error:
			raise SnapshotError("Invalid Decimal value in snapshot.") from error
	if tag == "float":
		try:
			return float.fromhex(value["hex"])
		except (KeyError, TypeError, ValueError) as error:
			raise SnapshotError("Invalid float value in snapshot.") from error
	if tag == "numpy_scalar":
		try:
			dtype = np.dtype(value["dtype"])
			if dtype.hasobject:
				raise SnapshotError("NumPy object scalar dtype is not supported.")
			decoded = _decode(value["value"])
			return np.array(decoded, dtype=dtype).reshape(())[()]
		except SnapshotError:
			raise
		except Exception as error:
			raise SnapshotError("Invalid NumPy scalar in snapshot.") from error
	if tag == "pandas.NA":
		return pd.NA
	if tag == "pandas.NaT":
		return pd.NaT
	if tag == "datetime":
		return datetime.fromisoformat(value["value"])
	if tag == "date":
		return date.fromisoformat(value["value"])
	if tag == "time":
		return time.fromisoformat(value["value"])
	if tag == "tuple":
		return tuple(_decode(item) for item in value["items"])
	if tag == "dict":
		return {_decode(key): _decode(item) for key, item in value["items"]}
	if tag == "dataclass":
		class_name = value.get("name")
		model = _DATACLASS_TYPES.get(class_name)
		if model is None:
			raise SnapshotError("Unsupported dataclass in snapshot: {0}".format(class_name))
		encoded_fields = value.get("fields")
		if not isinstance(encoded_fields, dict):
			raise SnapshotError("Dataclass fields must be a JSON object.")
		allowed_fields = {item.name for item in fields(model)}
		if set(encoded_fields) != allowed_fields:
			raise SnapshotError("Snapshot fields do not match model {0}.".format(class_name))
		try:
			return model(**{key: _decode(item) for key, item in encoded_fields.items()})
		except Exception as error:
			raise SnapshotError("Could not reconstruct model {0}.".format(class_name)) from error
	raise SnapshotError("Unsupported snapshot type tag: {0}".format(tag))


def _canonical_json(value: Any) -> bytes:
	try:
		text = json.dumps(
			value,
			ensure_ascii=False,
			sort_keys=True,
			separators=(",", ":"),
			allow_nan=False,
		)
	except (TypeError, ValueError) as error:
		raise SnapshotError("Snapshot content is not valid canonical JSON.") from error
	return text.encode("utf-8")


def serialize_credit_results(results: Iterable[CreditResult]) -> bytes:
	"""Serialize CreditResults into a checksummed, schema-versioned JSON document."""
	records = list(results)
	if any(not isinstance(record, CreditResult) for record in records):
		raise SnapshotError("Every snapshot record must be a CreditResult.")
	processing_run_ids = sorted(
		{record.processing_run_id for record in records if record.processing_run_id is not None}
	)
	unsigned = {
		"format": SNAPSHOT_FORMAT,
		"schema_version": SNAPSHOT_SCHEMA_VERSION,
		"record_count": len(records),
		"processing_run_ids": processing_run_ids,
		"records": [_encode(record) for record in records],
	}
	digest = hashlib.sha256(_canonical_json(unsigned)).hexdigest()
	snapshot = dict(unsigned)
	snapshot["sha256"] = digest
	return _canonical_json(snapshot)


def deserialize_credit_results(snapshot_data: bytes | str) -> Tuple[CreditResult, ...]:
	"""Validate snapshot format/checksum and reconstruct lossless CreditResult objects."""
	try:
		text = snapshot_data.decode("utf-8") if isinstance(snapshot_data, bytes) else snapshot_data
		if not isinstance(text, str):
			raise TypeError("Snapshot input must be bytes or text.")
		snapshot = json.loads(text)
	except (UnicodeDecodeError, TypeError, json.JSONDecodeError) as error:
		raise SnapshotError("Snapshot is not valid UTF-8 JSON.") from error
	if not isinstance(snapshot, dict):
		raise SnapshotError("Snapshot root must be a JSON object.")
	if snapshot.get("format") != SNAPSHOT_FORMAT:
		raise SnapshotError("Unrecognized snapshot format.")
	if snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
		raise SnapshotError("Unsupported snapshot schema version.")
	checksum = snapshot.get("sha256")
	if not isinstance(checksum, str):
		raise SnapshotError("Snapshot checksum is missing.")
	unsigned = {key: value for key, value in snapshot.items() if key != "sha256"}
	actual_checksum = hashlib.sha256(_canonical_json(unsigned)).hexdigest()
	if actual_checksum != checksum:
		raise SnapshotError("Snapshot checksum validation failed.")
	encoded_records = snapshot.get("records")
	if not isinstance(encoded_records, list):
		raise SnapshotError("Snapshot records must be a JSON array.")
	if snapshot.get("record_count") != len(encoded_records):
		raise SnapshotError("Snapshot record_count does not match records.")
	records = tuple(_decode(item) for item in encoded_records)
	if any(not isinstance(record, CreditResult) for record in records):
		raise SnapshotError("Snapshot contains a record other than CreditResult.")
	actual_run_ids = sorted(
		{record.processing_run_id for record in records if record.processing_run_id is not None}
	)
	if snapshot.get("processing_run_ids") != actual_run_ids:
		raise SnapshotError("Snapshot processing_run_ids do not match its records.")
	return records


def load_credit_results_snapshot(path: str | Path) -> Tuple[CreditResult, ...]:
	"""Load and validate a local snapshot file."""
	return deserialize_credit_results(Path(path).read_bytes())


def write_credit_results_snapshot(
	path: str | Path,
	results: Iterable[CreditResult],
	*,
	overwrite: bool = False,
) -> str:
	"""Atomically publish one local immutable-by-default snapshot and return its checksum."""
	target = Path(path)
	if target.exists() and not overwrite:
		raise FileExistsError("Refusing to replace existing snapshot: {0}".format(target))
	content = serialize_credit_results(results)
	target.parent.mkdir(parents=True, exist_ok=True)
	file_descriptor, temporary_name = tempfile.mkstemp(
		prefix=target.name + ".",
		suffix=".tmp",
		dir=str(target.parent),
	)
	try:
		with os.fdopen(file_descriptor, "wb") as temporary_file:
			temporary_file.write(content)
			temporary_file.flush()
			os.fsync(temporary_file.fileno())
		if target.exists() and not overwrite:
			raise FileExistsError("Refusing to replace existing snapshot: {0}".format(target))
		os.replace(temporary_name, target)
	finally:
		if os.path.exists(temporary_name):
			os.unlink(temporary_name)
	return json.loads(content.decode("utf-8"))["sha256"]


__all__ = [
	"SNAPSHOT_FORMAT",
	"SNAPSHOT_SCHEMA_VERSION",
	"SnapshotError",
	"deserialize_credit_results",
	"load_credit_results_snapshot",
	"serialize_credit_results",
	"write_credit_results_snapshot",
]