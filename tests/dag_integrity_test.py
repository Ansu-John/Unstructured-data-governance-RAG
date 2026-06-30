"""
Path: tests/dag_integrity_test.py
Description: Production testing module to validate DAG syntax, cycles, and load errors.
"""
import unittest
from airflow.models import DagBag

class TestDagIntegrity(unittest.TestCase):

    def setUp(self):
        """Initialize the local DagBag context before running validations."""
        self.dagbag = DagBag(dag_folder="dags", include_examples=False)

    def test_dag_serialization_and_load_errors(self):
        """Verifies that no files in the dags folder contain critical python syntax, missing import errors, or dependency cycles."""
        load_errors = self.dagbag.import_errors
        
        error_msg = "\n".join(
            [f"File: {filename} -> Error: {error}" for filename, error in load_errors.items()]
        )
        
        self.assertEqual(
            len(load_errors), 
            0, 
            msg=f"Airflow Import Failures Detected:\n{error_msg}"
        )