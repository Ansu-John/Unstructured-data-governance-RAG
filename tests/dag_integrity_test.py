"""
Path: tests/dag_integrity_test.py
Description: Production testing module to validate DAG syntax, cycles, and load errors.
"""

import unittest
from airflow.models import DagBag

class TestDagIntegrity(unittest.TestCase):

    def setUp(self):
        """Initialize the local DagBag context before running validations."""
        # Initialize DagBag without the deprecated read_dags_from_db parameter
        self.dagbag =  DagBag(dag_folder="dags", include_examples=False)

    def test_dag_serialization_and_load_errors(self):
        """Verifies that no files in the dags folder contain critical python syntax or missing import errors."""
        load_errors = self.dagbag.import_errors
        
        # Format the error dictionary clearly if any files failed to compile
        error_msg = "\n".join(
            [f"File: {filename} -> Error: {error}" for filename, error in load_errors.items()]
        )
        
        self.assertEqual(
            len(load_errors), 
            0, 
            msg=f"Airflow Import Failures Detected:\n{error_msg}"
        )

    def test_dag_dependency_cycles(self):
        """Ensures that no DAG contains a cyclical dependency loop (e.g., Task A -> Task B -> Task A)."""
        for dag_id, dag in self.dagbag.dags.items():
            # Airflow has a built-in cycle detection algorithm
            try:
                dag.test_cycle()
            except Exception as cycle_error:
                self.fail(f"Cyclic Dependency Discovered in DAG '{dag_id}': {cycle_error}")