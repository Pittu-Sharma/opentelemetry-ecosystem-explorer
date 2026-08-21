# Copyright The OpenTelemetry Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""Base inventory management for versioned artifact storage."""

import json
import logging
import re
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml
from semantic_version import Version

from .content_hashing import compute_content_hash

logger = logging.getLogger(__name__)


class BaseInventoryManager:
    """Base class for versioned inventory storage.

    Manages a flat inventory directory structure:
        inventory_dir/v{version}/

    Subclasses add domain-specific save/load methods.
    """

    def __init__(self, inventory_dir: str):
        """
        Args:
            inventory_dir: Base directory for versioned storage
        """
        self.inventory_dir = Path(inventory_dir)

    def get_version_dir(self, version: Version) -> Path:
        """
        Get the directory path for a specific version.

        Args:
            version: Version object

        Returns:
            Path to version directory (with 'v' prefix)
        """
        return self.inventory_dir / f"v{version}"

    def list_versions(self) -> list[Version]:
        """
        List all available versions.

        Returns:
            List of versions, sorted newest to oldest
        """
        if not self.inventory_dir.exists():
            return []

        versions = []
        for item in self.inventory_dir.iterdir():
            if item.is_dir():
                try:
                    # Parse version string, stripping 'v' prefix
                    # Handles "v1.0.0", "v1.0.1-SNAPSHOT"
                    version = Version(item.name.lstrip("v"))
                    versions.append(version)
                except ValueError:
                    # Skip directories that don't match version format
                    continue

        return sorted(versions, reverse=True)

    def list_snapshot_versions(self) -> list[Version]:
        """
        List all snapshot versions.

        Returns:
            List of snapshot versions
        """
        return [v for v in self.list_versions() if v.prerelease]

    def list_release_versions(self) -> list[Version]:
        """
        List all release (non-prerelease) versions.

        Returns:
            List of release versions, sorted newest to oldest
        """
        return [v for v in self.list_versions() if not v.prerelease]

    def cleanup_snapshots(self) -> int:
        """
        Remove all snapshot versions.

        Returns:
            Number of snapshot versions removed
        """
        snapshots = self.list_snapshot_versions()
        count = 0

        for snapshot in snapshots:
            snapshot_dir = self.get_version_dir(snapshot)
            if snapshot_dir.exists():
                shutil.rmtree(snapshot_dir)
                count += 1

        return count

    def version_exists(self, version: Version) -> bool:
        """
        Check if a specific version exists.

        Args:
            version: Version to check

        Returns:
            True if version directory exists
        """
        return self.get_version_dir(version).exists()

    def delete_version(self, version: Version) -> bool:
        """
        Delete a specific version directory.

        Args:
            version: Version to delete

        Returns:
            True if version was deleted, False if it didn't exist
        """
        version_dir = self.get_version_dir(version)
        if version_dir.exists():
            shutil.rmtree(version_dir)
            return True
        return False


class JavaagentInventoryManager(BaseInventoryManager):
    """Manages Java instrumentation inventory storage and retrieval."""

    FILE_NAME = "instrumentation.yaml"
    README_DIR = "library_readmes"

    def __init__(self, inventory_dir: str = "ecosystem-registry/java/javaagent"):
        """
        Args:
            inventory_dir: Base directory for versioned metadata
        """
        super().__init__(inventory_dir)

    def version_exists(self, version: Version) -> bool:
        """
        Check if a specific version exists.

        Args:
            version: Version to check

        Returns:
            True if version directory and instrumentation file exist
        """
        version_dir = self.get_version_dir(version)
        return version_dir.exists() and (version_dir / self.FILE_NAME).exists()

    def save_versioned_inventory(self, version: Version, instrumentations: dict[str, Any]) -> None:
        """
        Save inventory for a specific version.

        Args:
            version: Version object
            instrumentations: Instrumentation data dict
        """
        version_dir = self.get_version_dir(version)
        version_dir.mkdir(parents=True, exist_ok=True)

        file_path = version_dir / self.FILE_NAME

        inventory_data = {
            **instrumentations,
        }

        with open(file_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(inventory_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    def load_versioned_inventory(self, version: Version) -> dict[str, Any]:
        """
        Load inventory for a specific version.

        Args:
            version: Version object

        Returns:
            Inventory dictionary with full structure, or empty structure if it doesn't exist
        """
        version_dir = self.get_version_dir(version)
        file_path = version_dir / self.FILE_NAME

        if not file_path.exists():
            return {
                "file_format": 0.1,
                "libraries": [],
            }

        with open(file_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        if not isinstance(data, dict):
            raise ValueError(f"Inventory file for version {version} must contain a mapping")

        return data

    def _get_sync_state_path(self) -> Path:
        return self.inventory_dir / "readme-sync-state.json"

    def _load_sync_state(self) -> dict[str, Any]:
        path = self._get_sync_state_path()
        if not path.exists():
            return {"synced_versions": [], "failures": {}}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    data.setdefault("synced_versions", [])
                    data.setdefault("failures", {})
                    return data
        except (OSError, ValueError) as e:
            logger.warning("Failed to load sync state file: %s", e)
        return {"synced_versions": [], "failures": {}}

    def _save_sync_state(self, state: dict[str, Any]) -> None:
        path = self._get_sync_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, sort_keys=True)
                f.write("\n")
        except OSError as e:
            logger.error("Failed to save sync state file: %s", e)

    def readmes_synced(self, version: Version) -> bool:
        """Return True if the readmes have been fully synced for this version."""
        state = self._load_sync_state()
        return str(version) in state.get("synced_versions", [])

    def get_readme_failures(self, version: Version) -> dict[str, int]:
        """Get failure counts for libraries in the given version."""
        state = self._load_sync_state()
        failures = state.get("failures", {})
        return failures.get(str(version), {})

    def record_readme_sync(self, version: Version, failures: dict[str, int]) -> None:
        """Record the sync results. If failures is empty, mark version as synced."""
        state = self._load_sync_state()
        synced_versions = set(state.get("synced_versions", []))
        version_str = str(version)

        if not failures:
            synced_versions.add(version_str)
            if "failures" in state and version_str in state["failures"]:
                del state["failures"][version_str]
        else:
            synced_versions.discard(version_str)
            state.setdefault("failures", {})[version_str] = failures

        state["synced_versions"] = sorted(list(synced_versions))
        self._save_sync_state(state)

    def _sanitize_name(self, name: str) -> str:
        """Sanitizes a name for use as a filename to prevent path traversal."""
        return re.sub(r"[^a-zA-Z0-9._\-]", "_", name)

    def save_library_readmes(
        self,
        readmes: Iterable[tuple[str, str]],  # (library_name, content)
    ) -> dict[str, str]:
        """Write each README content-addressed to global folder. Returns mapping of name to filename."""
        target_dir = self.inventory_dir / self.README_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        written = {}
        for name, content in readmes:
            digest = compute_content_hash(content)
            safe_name = self._sanitize_name(name)
            filename = f"{safe_name}-{digest}.md"
            file_path = target_dir / filename
            if not file_path.exists():
                file_path.write_text(content, encoding="utf-8")
            written[name] = filename
        return written

    def _readme_map_from_yaml(self, version: Version) -> dict[str, str]:
        """
        Build a map of library_name -> markdown_hash by reading the 'readme'
        field from the version's instrumentation.yaml.

        Returns an empty dict if no library has a 'readme' field (pre-#883 state).
        """
        data = self.load_versioned_inventory(version)
        libraries_raw = data.get("libraries", [])
        if isinstance(libraries_raw, dict):
            libraries = [lib for group in libraries_raw.values() for lib in group]
        else:
            libraries = libraries_raw

        readme_map = {}
        for lib in libraries:
            readme_file = lib.get("readme")
            if readme_file:
                parsed = self._parse_readme_filename(readme_file)
                if parsed:
                    _, markdown_hash = parsed
                    # Use the raw name so callers can look it up by item["name"].
                    readme_map[lib["name"]] = markdown_hash
                else:
                    logger.warning(
                        "Malformed README filename '%s' for library '%s' in version %s – skipping.",
                        readme_file,
                        lib.get("name", "<unknown>"),
                        version,
                    )
        return readme_map

    def _legacy_readme_map(self, version: Version) -> dict[str, str]:
        """
        Fallback for pre-#883 versions: build a map of library_name -> markdown_hash
        by scanning the per-version library_readmes directory on disk.

        Tie-break on sorted filename when multiple files exist for the same name.
        Remove with the per-version library_readmes/ dirs once every version is
        migrated.
        """
        # Keys here are *sanitized* names recovered from filenames, unlike _readme_map_from_yaml
        # which keys by the raw name. Equivalent for all 261 current Java library names; would
        # diverge for any name containing a character outside [a-zA-Z0-9._-]. Goes away with the
        # legacy dirs.
        version_readme_dir = self.get_version_dir(version) / self.README_DIR
        if not version_readme_dir.exists():
            return {}

        readme_map: dict[str, str] = {}
        # Sort filenames for a deterministic tie-break order.
        for item in sorted(version_readme_dir.iterdir()):
            if not (item.is_file() and item.suffix == ".md"):
                continue
            parsed = self._parse_readme_filename(item.name)
            if parsed:
                name, markdown_hash = parsed
                readme_map[name] = markdown_hash
        return readme_map

    def load_library_readme_map(self, version: Version) -> dict[str, str]:
        """
        Build a map of library_name -> markdown_hash for a given version.

        Tries the new path first (reads 'readme' refs from instrumentation.yaml).
        Falls back to the legacy per-version library_readmes/ directory for
        pre-#883 versions that have no 'readme' fields yet.  Remove the fallback
        after every version has been migrated.

        Args:
            version: Version to scan

        Returns:
            Dictionary mapping library names to their markdown content hashes
        """
        readme_map = self._legacy_readme_map(version)
        readme_map.update(self._readme_map_from_yaml(version))
        return readme_map

    def load_library_readme_content(self, version: Version, library_name: str, markdown_hash: str) -> str | None:
        """
        Load the content of a specific library README.

        Tries the global library_readmes/ directory first (post-#883 path).
        Falls back to the per-version library_readmes/ directory for versions
        that have not yet been migrated.  Remove the fallback once every version
        directory has been cleaned up.

        Args:
            version: Version to load from (used only for the legacy fallback)
            library_name: Name of the library
            markdown_hash: Content hash of the markdown

        Returns:
            The markdown content, or None if it doesn't exist or cannot be read
        """
        safe_name = self._sanitize_name(library_name)
        filename = f"{safe_name}-{markdown_hash}.md"

        # New (global) path.
        file_path = self.inventory_dir / self.README_DIR / filename
        if not file_path.exists():
            # Legacy per-version path – pre-#883 layout.
            file_path = self.get_version_dir(version) / self.README_DIR / filename

        if not file_path.exists():
            return None

        try:
            return file_path.read_text(encoding="utf-8")
        except OSError as e:
            logger.error("Failed to read README file '%s': %s", file_path, e)
            return None

    def _parse_readme_filename(self, filename: str) -> tuple[str, str] | None:
        """
        Parse a README filename into (library_name, markdown_hash).
        Format: {library-name}-{hash}.md
        """
        match = re.match(r"^(.+)-([a-f0-9]{12})\.md$", filename)
        if match:
            return match.group(1), match.group(2)
        return None

    def prune_orphan_readmes(self) -> int:
        """
        Removes any readme files in the global library_readmes directory
        that are not referenced by any version's instrumentation.yaml.

        Only inspects entries under the 'libraries' key, not 'custom'.
        Returns 0 (and removes nothing) when no version references any README,
        which is the state of the registry before migration – deleting every
        file in that case would be wrong.

        Returns:
            Number of files removed
        """
        readme_dir = self.inventory_dir / self.README_DIR
        if not readme_dir.exists():
            return 0

        referenced_files: set[str] = set()
        for version in self.list_versions():
            data = self.load_versioned_inventory(version)
            libraries_raw = data.get("libraries", [])

            if isinstance(libraries_raw, dict):
                libraries = [lib for group in libraries_raw.values() for lib in group]
            else:
                libraries = libraries_raw

            for lib in libraries:
                if readme_file := lib.get("readme"):
                    referenced_files.add(readme_file)

        # Guard: if no version references any README (e.g. pre-migration registry),
        # do NOT touch the directory – deleting everything would be destructive.
        if not referenced_files:
            return 0

        removed = 0
        for item in readme_dir.iterdir():
            if item.is_file() and item.suffix == ".md" and item.name not in referenced_files:
                try:
                    item.unlink()
                    removed += 1
                except OSError as e:
                    logger.warning("Failed to delete orphaned README %s: %s", item, e)

        return removed
