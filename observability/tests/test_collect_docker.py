from __future__ import annotations

import json
import unittest

from observability.ci.collect_docker import _parse_images


class DockerCollectorTests(unittest.TestCase):
    def test_compose_v5_image_array_derives_service_from_container_name(self) -> None:
        output = json.dumps(
            [
                {
                    "ID": "sha256:abc",
                    "ContainerName": "eve-trade-integration-api-gateway-1",
                    "Repository": "eve-trade-api-gateway",
                    "Tag": "latest",
                }
            ]
        )

        images = _parse_images(output, ["api-gateway", "market"])

        self.assertEqual(images[0]["docker.compose_service"], "api-gateway")
        self.assertEqual(images[0]["docker.image_digest"], "sha256:abc")


if __name__ == "__main__":
    unittest.main()
