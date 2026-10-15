"""profile-from-resume derives its output schema from the example profile."""
import json
import os
import unittest

import run


class ProfileSchemaTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(run.CONFIG, "profile.example.json"), encoding="utf-8") as fh:
            self.template = json.load(fh)
        self.keys = [k for k in self.template if not k.startswith("_")]
        self.schema = run.profile_schema(self.template, self.keys)

    def test_every_object_is_closed(self):
        """The API rejects any object schema without additionalProperties: false.
        A bare {"type": "object"} for thresholds made every call fail with a 400."""
        def walk(node, path):
            if node.get("type") == "object":
                self.assertIs(node.get("additionalProperties"), False, path)
                self.assertEqual(set(node["required"]), set(node["properties"]), path)
                for k, v in node["properties"].items():
                    walk(v, f"{path}.{k}")
        walk(self.schema, "profile")

    def test_booleans_are_not_typed_as_integers(self):
        self.assertEqual(self.schema["properties"]["us_only_remote"], {"type": "boolean"})
        self.assertEqual(self.schema["properties"]["comp_floor"], {"type": "integer"})
        self.assertEqual(self.schema["properties"]["thresholds"]["properties"]["strong"], {"type": "integer"})


if __name__ == "__main__":
    unittest.main()
