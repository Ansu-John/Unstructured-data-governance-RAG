import os
import pytest
from pyspark.sql import SparkSession
from pyspark.sql.functions import col

from src.phase1_awss3.pdf_data_extractor import process_pdf_directory

# --- FIXTURE: Spin up a local Spark Session ---
@pytest.fixture(scope="session")
def spark():
    # Force local networking to avoid Ubuntu topology errors
    os.environ["SPARK_LOCAL_IP"] = "127.0.0.1"
    os.environ["SPARK_LOCAL_HOSTNAME"] = "127.0.0.1"
    
    spark_session = SparkSession.builder \
        .appName("PySpark-Pipeline-Testing") \
        .master("local[1]") \
        .getOrCreate()
        
    yield spark_session
    
    # Tear down Spark when tests finish
    spark_session.stop()

# --- FIXTURE: Run the pipeline to get the DataFrame ---
@pytest.fixture(scope="module")
def pipeline_df(spark):
    """Executes the main pipeline function against the local fixtures folder."""
    # Point Spark to the local fixtures directory containing sample PDFs
    current_dir = os.path.dirname(__file__)
    fixtures_path = os.path.join(current_dir, "../data/")
    
    # Trigger the pipeline!
    df = process_pdf_directory(spark, fixtures_path)
    return df

# --- THE TESTS ---

def test_pipeline_execution(pipeline_df):
    """Ensure the pipeline ran and returned data."""
    # Assuming you have 2 sample PDFs in your fixtures folder
    assert pipeline_df.count() >= 1, "Pipeline returned an empty DataFrame!"

def test_pipeline_schema(pipeline_df):
    """Ensure all expected columns exist in the final output."""
    expected_columns = [
        "File_Name", "Extraction_Time", "Contractor_Name", "Contract_Number", "Service", "Direct_Cost_Line_Item",
        "Direct_Cost_Explanation", "Indirect_Cost_Line_Item", "Indirect_Cost_Explanation", "Unpaid_Obligations",
        "Unpaid_Obligations_Reason", "Included_In_Report", "Completed_by", "Title_1", "Phone",
        "Signature", "Title_2", "Date"
    ]
    
    actual_columns = pipeline_df.columns
    for col_name in expected_columns:
        assert col_name in actual_columns, f"Missing column in output DataFrame: {col_name}"

def test_pipeline_data_integrity(pipeline_df):
    """Ensure no critical pipeline failures resulted in widespread Nulls."""
    null_contractors = pipeline_df.filter(col("Contractor_Name").isNull()).count()
    assert null_contractors == 0, "Pipeline produced Null Contractor Names. UDF may have failed."
