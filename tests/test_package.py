"""Offline package integration tests with a minimal ComfyUI server fixture."""

import ast
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest import mock
import uuid

from aiohttp import web


ROOT = Path(__file__).resolve().parents[1]


class PackageAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.package_name = "plamo_package_acceptance_" + uuid.uuid4().hex

    def tearDown(self):
        for name in list(sys.modules):
            if name == self.package_name or name.startswith(self.package_name + "."):
                sys.modules.pop(name, None)

    def load_package(self, server):
        spec = importlib.util.spec_from_file_location(
            self.package_name,
            ROOT / "__init__.py",
            submodule_search_locations=[str(ROOT)],
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[self.package_name] = module
        with mock.patch.dict(sys.modules, {"server": server}):
            with mock.patch("socket.socket.connect", side_effect=AssertionError("Import must not contact LM Studio")):
                spec.loader.exec_module(module)
        return module

    @staticmethod
    def fake_server(instance):
        server = types.ModuleType("server")
        server.PromptServer = type("PromptServer", (), {"instance": instance})
        return server

    def test_comfyui_import_registers_exactly_one_node_and_three_routes(self):
        # The host is a fixture; its route table is the actual aiohttp type.
        routes = web.RouteTableDef()
        instance = types.SimpleNamespace(routes=routes, app=types.SimpleNamespace(on_cleanup=[]))
        server = self.fake_server(instance)
        package = self.load_package(server)
        self.assertEqual(set(package.NODE_CLASS_MAPPINGS), {"PLaMoPromptTranslator"})
        self.assertEqual(set(package.NODE_DISPLAY_NAME_MAPPINGS), {"PLaMoPromptTranslator"})
        self.assertTrue(package.NODE_DISPLAY_NAME_MAPPINGS["PLaMoPromptTranslator"])
        self.assertEqual({route.path for route in routes}, {
            "/plamo_translator/v1/models",
            "/plamo_translator/v1/translate",
            "/plamo_translator/v1/unload",
        })
        for route in routes:
            self.assertEqual(route.method, "POST")
            self.assertTrue(callable(route.handler))
        package.routes.register_routes(instance)
        self.assertEqual(len(routes), 3, "Registering again must not duplicate routes")

    def test_import_without_initialized_server_still_exposes_offline_node(self):
        package = self.load_package(self.fake_server(None))
        node_type = package.NODE_CLASS_MAPPINGS["PLaMoPromptTranslator"]
        self.assertEqual(node_type.RETURN_TYPES, ("STRING",))

    def test_web_directory_points_to_bundled_frontend(self):
        package = self.load_package(self.fake_server(None))
        web = (ROOT / package.WEB_DIRECTORY).resolve()
        self.assertTrue(web.is_relative_to(ROOT.resolve()))
        self.assertTrue(web.is_dir())
        self.assertTrue((web / "plamo.js").is_file())
        self.assertGreater((web / "plamo.js").stat().st_size, 0)

    def test_runtime_does_not_import_forge_or_extra_llm_libraries(self):
        # A first-version installation must use the host's aiohttp and stdlib.
        forbidden = {"gradio", "modules", "openai", "lmstudio", "httpx", "transformers", "llama_cpp", "torch"}
        violations = []
        for path in sorted(ROOT.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                imports = []
                if isinstance(node, ast.Import):
                    imports = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imports = [node.module.split(".")[0]]
                for name in imports:
                    if name in forbidden:
                        violations.append(f"{path.name}:{node.lineno}: {name}")
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
