"""Write an access world to files the collectors read, and the answer key apart.

Same stable JSON as tier0's export. No wall clock, so the same seed gives identical bytes.
"""

import csv
import hashlib
import io
from dataclasses import asdict
from pathlib import Path
from typing import Any

from meridian_dataset.access.model import AccessWorld, AppExport
from meridian_dataset.export import _dump

_SLACK_LIST = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
_SLACK_USER = "urn:ietf:params:scim:schemas:core:2.0:User"


def csv_text(export: AppExport) -> str:
    """The export as text: preamble lines, header, rows, footer lines, all ending in CRLF."""
    buffer = io.StringIO(newline="")
    for line in export.preamble:
        buffer.write(line + "\r\n")
    writer = csv.writer(
        buffer,
        delimiter=export.delimiter,
        quoting=csv.QUOTE_ALL if export.quote_all else csv.QUOTE_MINIMAL,
        lineterminator="\r\n",
    )
    writer.writerow(export.header)
    writer.writerows(export.rows)
    for line in export.footer:
        buffer.write(line + "\r\n")
    return buffer.getvalue()


def _csv_bytes(export: AppExport) -> bytes:
    return csv_text(export).encode(export.encoding)


def _contents(world: AccessWorld) -> dict[str, bytes]:
    events: list[dict[str, Any]] = []
    for e in world.hris.events:
        item = asdict(e)
        item["fields"] = dict(e.fields)
        events.append(item)
    okta = world.okta
    contents: dict[str, bytes] = {
        "hris.json": _dump(
            {
                "seed": world.seed,
                "generator_version": world.generator_version,
                "reference_date": world.reference_date,
                "records": [asdict(r) for r in world.hris.records],
                "events": events,
            }
        ),
        "okta.json": _dump(
            {
                "users": [
                    {
                        "id": u.id,
                        "status": u.status,
                        "created": u.created,
                        "lastLogin": u.last_login,
                        "profile": {
                            "login": u.login,
                            "email": u.email,
                            "firstName": u.first_name,
                            "lastName": u.last_name,
                        },
                    }
                    for u in okta.users
                ],
                "apps": [
                    {"id": a.id, "label": a.label, "name": a.app_id, "status": "ACTIVE"}
                    for a in okta.apps
                ],
                "assignments": [
                    {
                        "appId": a.app_id,
                        "userId": a.user_id,
                        "created": a.created,
                        "scope": a.scope,
                        "profile": {"role": a.role},
                    }
                    for a in okta.assignments
                ],
                "system_log": [
                    {
                        "uuid": e.uuid,
                        "published": e.published,
                        "eventType": e.event_type,
                        "actor": {"alternateId": e.actor_alternate_id, "type": e.actor_type},
                        "transaction": {"detail": {"via": e.via}},
                        "target": [
                            {"type": "AppInstance", "id": e.target_app_id},
                            {
                                "type": "User",
                                "id": e.target_user_id,
                                "alternateId": e.target_user_login,
                            },
                        ],
                    }
                    for e in okta.log
                ],
            }
        ),
        "google.json": _dump(
            {
                "users": [
                    {
                        "id": g.id,
                        "primaryEmail": g.primary_email,
                        "name": {
                            "givenName": g.given_name,
                            "familyName": g.family_name,
                            "fullName": g.full_name,
                        },
                        "suspended": g.suspended,
                        "orgUnitPath": g.org_unit_path,
                        "aliases": list(g.aliases),
                        "creationTime": g.creation_time,
                        "lastLoginTime": g.last_login_time,
                    }
                    for g in world.google_users
                ]
            }
        ),
        "slack_scim.json": _dump(
            {
                "schemas": [_SLACK_LIST],
                "totalResults": len(world.slack_users),
                "Resources": [
                    {
                        "schemas": [_SLACK_USER],
                        "id": s.id,
                        "userName": s.user_name,
                        "displayName": s.display_name,
                        "emails": [{"value": s.email, "primary": True}],
                        "active": s.active,
                    }
                    for s in world.slack_users
                ],
            }
        ),
        "answer_key.json": _dump(asdict(world.answer_key)),
    }
    for export in world.exports:
        contents[f"exports/{export.file_name}"] = _csv_bytes(export)
    return contents


def write_access(world: AccessWorld, out_dir: Path) -> dict[str, Path]:
    """Write every file into out_dir and return {relative name: path}, manifest included."""
    (out_dir / "exports").mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    hashes: dict[str, str] = {}
    for name, data in sorted(_contents(world).items()):
        path = out_dir / name
        path.write_bytes(data)
        paths[name] = path
        hashes[name] = hashlib.sha256(data).hexdigest()
    manifest = out_dir / "manifest.json"
    manifest.write_bytes(
        _dump(
            {
                "seed": world.seed,
                "generator_version": world.generator_version,
                "recipe_name": world.recipe_name,
                "files": dict(sorted(hashes.items())),
            }
        )
    )
    paths["manifest.json"] = manifest
    return paths
