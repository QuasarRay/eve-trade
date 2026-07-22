from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from render_release_kubernetes import load_lock, render
from verify_rendered_kubernetes import image_reference_error


REPOSITORY = "QuasarRay/eve-trade"
SHA = "5161a1a12bed9ebb589551662067cbbd3bcfb81d"


def image_lock(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "eve-trade.image-lock/v1",
        "repository": REPOSITORY,
        "sha": SHA,
        "images": {
            name: f"ghcr.io/quasarray/eve-trade-{name}@sha256:{index:064x}"
            for index, name in enumerate(("encore-backend", "trade-settlement", "quilkin"), start=1)
        },
    }
    value.update(overrides)
    return value


def write_json(root: Path, value: object) -> Path:
    path = root / "image-lock.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class ProductionImagePolicyTests(unittest.TestCase):
    def test_rejects_all_zero_digest(self) -> None:
        image = f"registry.invalid/app@sha256:{'0' * 64}"
        self.assertEqual(image_reference_error(image), "placeholder digest")

    def test_rejects_repeated_character_digest(self) -> None:
        image = f"registry.invalid/app@sha256:{'a' * 64}"
        self.assertEqual(image_reference_error(image), "placeholder digest")

    def test_rejects_known_sentinel_digest(self) -> None:
        image = f"registry.invalid/app@sha256:{'deadbeef' * 8}"
        self.assertEqual(image_reference_error(image), "placeholder digest")

    def test_rejects_placeholder_registry(self) -> None:
        image = f"registry.example.com/app@sha256:{'1234abcd' * 8}"
        self.assertEqual(image_reference_error(image), "placeholder registry")

    def test_accepts_immutable_non_placeholder_reference(self) -> None:
        image = f"ghcr.io/example/app@sha256:{'1234abcd' * 8}"
        self.assertIsNone(image_reference_error(image))


class ReleaseImageLockTests(unittest.TestCase):
    def test_lock_is_bound_to_exact_repository_and_sha(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = write_json(root, image_lock())
            self.assertEqual(set(load_lock(path, REPOSITORY, SHA)), {"encore-backend", "trade-settlement", "quilkin"})
            with self.assertRaisesRegex(ValueError, "repository"):
                load_lock(path, "someone/else", SHA)
            with self.assertRaisesRegex(ValueError, "SHA"):
                load_lock(path, REPOSITORY, "a" * 40)

    def test_lock_rejects_mutable_or_incomplete_images(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = image_lock(images={"encore-backend": "ghcr.io/quasarray/app:latest"})
            with self.assertRaisesRegex(ValueError, "exactly"):
                load_lock(write_json(root, missing), REPOSITORY, SHA)

            mutable = image_lock()
            assert isinstance(mutable["images"], dict)
            mutable["images"]["encore-backend"] = "ghcr.io/quasarray/app:latest"
            with self.assertRaisesRegex(ValueError, "mutable"):
                load_lock(write_json(root, mutable), REPOSITORY, SHA)

    def test_renderer_replaces_each_exact_workload_target_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.yaml"
            resources = [
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "metadata": {"name": name},
                    "spec": {
                        "template": {
                            "spec": {
                                "containers": [{"name": name, "image": f"eve-trade/{name}:dev"}],
                            },
                        },
                    },
                }
                for name in ("encore-backend", "trade-settlement", "quilkin")
            ]
            manifest.write_text(yaml.safe_dump_all(resources), encoding="utf-8")
            images = image_lock()["images"]
            assert isinstance(images, dict)
            rendered = render(manifest, {str(key): str(value) for key, value in images.items()})
            actual = {
                container["name"]: container["image"]
                for resource in rendered
                for container in resource["spec"]["template"]["spec"]["containers"]
            }
            self.assertEqual(actual, images)


class LocalPostgresRuntimeTests(unittest.TestCase):
    def test_pgdata_is_owned_child_of_emptydir_mount(self) -> None:
        manifest = (
            Path(__file__).resolve().parents[2]
            / "distributed-backend"
            / "orchestration"
            / "kubernetes"
            / "overlay"
            / "local"
            / "postgres.yaml"
        )
        resources = list(yaml.safe_load_all(manifest.read_text(encoding="utf-8")))
        deployment = next(resource for resource in resources if resource["kind"] == "Deployment")
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        env = {item["name"]: item["value"] for item in container["env"]}

        self.assertEqual(env["PGDATA"], "/var/lib/postgresql/data/pgdata")
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertEqual(deployment["spec"]["template"]["spec"]["securityContext"]["runAsUser"], 999)


class LocalNSQRuntimeTests(unittest.TestCase):
    def test_local_patch_preserves_writable_data_mount_when_tls_is_removed(self) -> None:
        manifest = (
            Path(__file__).resolve().parents[2]
            / "distributed-backend"
            / "orchestration"
            / "kubernetes"
            / "overlay"
            / "local"
            / "nsq-local.yaml"
        )
        stateful_set = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        container = stateful_set["spec"]["template"]["spec"]["containers"][0]
        mounts = {item["name"]: item for item in container["volumeMounts"]}

        self.assertEqual(mounts["data"]["mountPath"], "/data")
        self.assertEqual(mounts["tls"]["$patch"], "delete")


if __name__ == "__main__":
    unittest.main()
