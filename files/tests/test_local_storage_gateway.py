from django.test import SimpleTestCase

from files.services.local_storage_gateway import StorageGateway


class StorageGatewayPathSafetyTests(SimpleTestCase):
    """No real call site today builds a key from anything but a UUID/int FK
    id, so this is defense-in-depth rather than a fix for a reachable bug --
    but the join boundary itself should refuse to resolve outside local_dir
    regardless of what a future caller passes it."""

    def setUp(self):
        self.gateway = StorageGateway()

    def test_absolute_path_key_is_rejected(self):
        with self.assertRaises(ValueError):
            self.gateway._safe_join("/etc/passwd")

    def test_parent_traversal_key_is_rejected(self):
        with self.assertRaises(ValueError):
            self.gateway._safe_join("../../../etc/passwd")

    def test_ordinary_key_still_resolves_inside_local_dir(self):
        full = self.gateway._safe_join("Silvora/tenants/1/users/2/files/abc/chunks/chunk_0.bin")
        self.assertTrue(full.startswith(self.gateway.local_dir))
