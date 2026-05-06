import os
from pyspark.sql import SparkSession

def main():        
    spark = SparkSession.builder \
        .appName("S3_Data_Explorer") \
        .master("local[*]") \
        .config("spark.jars.packages", "org.apache.hadoop:hadoop-aws:3.4.1") \
        .config("spark.hadoop.fs.s3a.aws.credentials.provider", "com.amazonaws.auth.InstanceProfileCredentialsProvider") \
        .config("spark.hadoop.fs.s3a.connection.timeout", "60000") \
        .config("spark.hadoop.fs.s3a.connection.establish.timeout", "5000") \
        .getOrCreate()

    # Point directly to your new Bronze extraction folder
    data_path = os.getenv("S3_OUTPUT_PATH")
    
    print(f"\nLoading JSON data from: {data_path} ...")
    
    # Read the JSON files. 
    # multiline=True is critical if the JSON objects span multiple lines.
    df = spark.read.option("multiline", "false").json(data_path)

    # ==========================================
    # TOOL 1: THE SCHEMA CHECKER
    # ==========================================
    print("\n--- 1. DATA SCHEMA ---")
    # This is vital for JSON. It will map out all nested keys and arrays.
    df.printSchema()

    # ==========================================
    # TOOL 2: THE QUICK PEEK
    # ==========================================
    print("\n--- 2. FIRST 3 ROWS ---")
    # We use truncate=False so nested JSON values aren't cut off
    df.show(3, truncate=False)

    # ==========================================
    # TOOL 3: SPARK SQL EXPLORATION
    # ==========================================
    print("\n--- 3. SQL QUERY TEST ---")
    df.createOrReplaceTempView("bronze_json_table")
    
    # A simple verification query to ensure records are readable
    spark.sql("""
        SELECT COUNT(*) as total_json_records 
        FROM bronze_json_table
    """).show()

    # Note: Once you run Tool 1 (printSchema) and see the exact column names 
    # your JSON generated, you can add more specific SQL queries here!

    spark.stop()

if __name__ == "__main__":
    main()
