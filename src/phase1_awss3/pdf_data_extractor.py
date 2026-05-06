import os

# 1. Force Python/OS level networking and tell the JVM to STRICTLY use IPv4
os.environ["SPARK_LOCAL_IP"] = "127.0.0.1"
os.environ["SPARK_LOCAL_HOSTNAME"] = "127.0.0.1"
os.environ["PYSPARK_SUBMIT_ARGS"] = "--driver-java-options '-Djava.net.preferIPv4Stack=true' pyspark-shell"

import re
import io
from datetime import datetime
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, udf, current_timestamp, split, element_at
from pyspark.sql.types import StructType, StructField, StringType
import pdfplumber
    
#spark.sparkContext.setLogLevel("DEBUG")

# 2. Define the schema for the extracted fields
extraction_schema = StructType([
    StructField("Contractor_Name", StringType(), True),
    StructField("Contract_Number", StringType(), True),
    StructField("Service", StringType(), True),
    StructField("Direct_Cost_Line_Item", StringType(), True),
    StructField("Direct_Cost_Explanation", StringType(), True),
    StructField("Indirect_Cost_Line_Item", StringType(), True),
    StructField("Indirect_Cost_Explanation", StringType(), True),
    StructField("Unpaid_Obligations", StringType(), True),
    StructField("Unpaid_Obligations_Explanation", StringType(), True),
    StructField("Included_In_Report", StringType(), True),
    StructField("Completed_by", StringType(), True),
    StructField("Completed_by_Title", StringType(), True),
    StructField("Phone", StringType(), True),
    StructField("Signature", StringType(), True),
    StructField("Signature_Title", StringType(), True),
    StructField("Date", StringType(), True)
])

# 3. Define the Python function to parse the PDF binary content
def parse_pdf_content(binary_content):
    
    try:
        text = ""
        tables = []
        
        # Open the binary stream with pdfplumber
        with pdfplumber.open(io.BytesIO(binary_content)) as pdf:
            for page in pdf.pages:
                # Extract text for regex operations
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
                
                # Extract structural tables for the cost blocks
                page_tables = page.extract_tables()
                for t in page_tables:
                    if t:
                        tables.append(t)
                        
        # Helper: Standard Regex Extraction
        def extract(pattern, string):
            match = re.search(pattern, string, re.DOTALL | re.IGNORECASE)
            if match:
                val = match.group(1)
                val = re.sub(r'[\n|]', ' ', val)  
                val = re.sub(r'\s+', ' ', val)    
                return val.strip(' “"”')
            return None

        # Helper: Parse Checkboxes into clean Yes/No strings
        def extract_checkbox(pattern, string):
            raw_val = extract(pattern, string)
            if raw_val:
                if "[X] Yes" in raw_val or "[x] Yes" in raw_val or "☒ Yes" in raw_val: 
                    return "Yes"
                if "[X] No" in raw_val or "[x] No" in raw_val or "☒ No" in raw_val: 
                    return "No"
                return raw_val # Fallback if unchecked
            return None

        # --- PROCESS TABLES ---
        direct_items, direct_exps = [], []
        indirect_items, indirect_exps = [], []

        for table in tables:
            # Skip empty tables
            if not table or not table[0]: continue
            
            # Check the headers to identify which table we are in
            headers = [str(h).lower().replace('\n', ' ') for h in table[0] if h]
            is_direct = any("direct cost" in h and "indirect" not in h for h in headers)
            is_indirect = any("indirect cost" in h for h in headers)

            if is_direct or is_indirect:
                for row in table[1:]: # Skip the header row
                    if len(row) >= 2:
                        # Clean cell values
                        item = str(row[0]).replace('\n', ' ').strip() if row[0] else ""
                        exp = str(row[1]).replace('\n', ' ').strip() if row[1] else ""
                        
                        if item or exp: 
                            if is_direct:
                                direct_items.append(item)
                                direct_exps.append(exp)
                            elif is_indirect:
                                indirect_items.append(item)
                                indirect_exps.append(exp)

        # --- ASSEMBLE DICTIONARY ---
        data = {
            "Contractor_Name": extract(r'Contractor\s*Name\s*:(.*?)(?=Contract)', text),
            "Contract_Number": extract(r'Contract\s*Number\s*:(.*?)(?=Service)', text),
            "Service": extract(r'Service\s*:(.*?)(?=EXPLANATION)', text),
            
            # Join the arrays with a newline so they map neatly to the DataFrame columns
            "Direct_Cost_Line_Item": "\n".join(direct_items) if direct_items else None,
            "Direct_Cost_Explanation": "\n".join(direct_exps) if direct_exps else None,
            "Indirect_Cost_Line_Item": "\n".join(indirect_items) if indirect_items else None,
            "Indirect_Cost_Explanation": "\n".join(indirect_exps) if indirect_exps else None,
            
            # Apply the new checkbox helper
            "Unpaid_Obligations": extract_checkbox(r'unpaid\s*obligations\s*\?\s*(.*?)(?=Please\s*explain)', text),
            "Unpaid_Obligations_Explanation": extract(r'Please\s*explain\s*:\s*(.*?)(?=If\s*yes)', text),
            "Included_In_Report": extract_checkbox(r'month\'s\s*report\s*\?\s*(.*?)(?=\*|Completed)', text),
            
            "Completed_by": extract(r'Completed\s*by\s*:(.*?)(?=Title)', text),
            "Completed_by_Title": extract(r'Completed\s*by.*?Title\s*:\s*(.*?)(?=Phone)', text),
            "Phone": extract(r'Phone\s*:(.*?)(?=Signature\s*\(Required)', text),
            
            # Anchored explicitly to the full phrase to ignore the "SIGNATURES" header
            "Signature": extract(r'Signature\s*\(Required\s*for\s*processing\)\s*:\s*Signed\s*electronically\s*by\s*(.*?)(?=Title)', text),
            
            # Bounded strictly between the second Title and the Date
            "Signature_Title": extract(r'Signature\s*\(Required\s*for\s*processing\).*?Title\s*:\s*(.*?)(?=Date)', text),
            "Date": extract(r'Date\s*:(.*?)(?=["“”]?By\s*signing|\Z)', text)
        }
        
        return tuple(data.values())
        
    except Exception as e:
        # Handles UDF crashes gracefully without breaking the Spark job
        return tuple([f"UDF CRASH: {str(e)}"] + [None] * 15)

# 4. Register the UDF
extract_pdf_udf = udf(parse_pdf_content, extraction_schema)

# 5. WRAP THE PIPELINE IN A FUNCTION
def process_pdf_directory(spark, input_path):
    """Reads PDFs from a path, extracts data, and returns the final DataFrame."""
    
    # Read binary files from S3 using pathGlobFilter
    df_raw = spark.read.format("binaryFile").option("pathGlobFilter", "*.pdf").load(input_path)
    #df_raw.select("content").show(truncate=False)
    
    # Apply UDF and add the required metadata columns
    df_extracted = df_raw.withColumn("parsed_data", extract_pdf_udf(col("content"))) \
        .withColumn("File_Name", element_at(split(col("path"), "/"), -1)) \
        .withColumn("Extraction_Time", current_timestamp())
    df_extracted.select("parsed_data").show(truncate=False)
        
    # Flatten the Struct schema into top-level columns
    df_final = df_extracted.select(
        col("File_Name"),
        col("Extraction_Time"),
        col("parsed_data.Contractor_Name"),
        col("parsed_data.Contract_Number"),
        col("parsed_data.Service"),
        col("parsed_data.Direct_Cost_Line_Item"),
        col("parsed_data.Direct_Cost_Explanation"),
        col("parsed_data.Indirect_Cost_Line_Item"),
        col("parsed_data.Indirect_Cost_Explanation"),
        col("parsed_data.Unpaid_Obligations"),
        col("parsed_data.Unpaid_Obligations_Explanation").alias("Unpaid_Obligations_Reason"),
        col("parsed_data.Included_In_Report"),
        col("parsed_data.Completed_by"),
        col("parsed_data.Completed_by_Title").alias("Title_1"),
        col("parsed_data.Phone"),
        col("parsed_data.Signature"),
        col("parsed_data.Signature_Title").alias("Title_2"),
        col("parsed_data.Date")
    )
    
    print("df_final count = {0}".format(str(df_final.count())))
    
    df_final.show(truncate=False)
    return df_final
    
# 6. ONLY EXECUTE IF RUN DIRECTLY
if __name__ == "__main__":
    # Standard production execution
    spark = SparkSession.builder \
    .appName("S3_PDF_Data_Extraction") \
    .master("local[*]") \
    .config("spark.driver.host", "127.0.0.1") \
    .config("spark.driver.bindAddress", "127.0.0.1") \
    .config("spark.jars.packages", "org.apache.hadoop:hadoop-aws:3.4.1") \
    .config("spark.hadoop.fs.s3a.aws.credentials.provider", "com.amazonaws.auth.InstanceProfileCredentialsProvider") \
    .getOrCreate()
    
    INPUT_S3 = os.getenv("S3_INPUT_PATH")
    OUTPUT_S3 = os.getenv("S3_OUTPUT_PATH")
    
    # Generate the dataframe
    final_df = process_pdf_directory(spark, INPUT_S3)
    
    # Write the results back to S3 as JSON
    final_df.write.mode("overwrite").json(OUTPUT_S3)
    print("Pipeline Complete.")
