import os
import pytest

# Assuming your project structure has pdfReader.py inside a 'src' folder
from src.phase1_awss3.pdf_data_extractor import parse_pdf_content

def test_parse_pdf_content_regex():
    """Validates that the UDF logic extracts data correctly without needing PySpark"""
    
    # 1. Read a local sample PDF (You must place a real PDF in this location)
    current_dir = os.path.dirname(__file__)
    file_path = os.path.join(current_dir, "../data/Quarterly_Report_Test_1.pdf")
    
    with open(file_path, "rb") as f:
        binary_content = f.read()
        
    # 2. Feed the binary data directly into the function
    result = parse_pdf_content(binary_content)
    
    # 3. Assert the result did not crash
    assert "UDF CRASH:" not in str(result), f"Function crashed: {result[0]}"
    
    # 4. Assert specific tuple indices match your expected test document
    # Tuple Index Map based on your script:
    # 0: Contractor_Name, 1: Contract_Number, 2: Service, 3: Direct_Line_Item, 
    # 4: Direct_Exp, 5: Indirect_Line_Item, 6: Indirect_Exp, 7: Unpaid, 
    # 8: Unpaid_Exp, 9: Included, 10: Completed_by, 11: Title_1, 
    # 12: Phone, 13: Signature, 14: Title_2, 15: Date
    
    # Example assertions based on the "Silver Meals Inc." sample
    assert result[0] == "Silver Meals Inc.", "Failed to extract Contractor Name"
    assert result[1] == "AAA-26-001", "Failed to extract Contract Number"
    assert result[7] == "Yes", "Failed to extract Unpaid Obligations boolean"
    assert result[15] == "05/04/2026", "Failed to extract Date"
