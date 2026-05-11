
import asyncio
import os
import sys

# Add the project root to sys.path so we can import app
sys.path.append(os.getcwd())

from app.crud.student_performance_support import generate_certificate_file

async def test():
    print("Attempting to generate test certificate...")
    try:
        file_id = await generate_certificate_file("Test Student", "Test Course")
        print(f"SUCCESS! Created file: {file_id}")
        
        upload_dir = os.path.join(os.getcwd(), "uploads", "certificate")
        file_path = os.path.join(upload_dir, file_id)
        
        if os.path.exists(file_path):
            print(f"Verified: File exists at {file_path}")
        else:
            print(f"ERROR: Function returned success, but file is MISSING at {file_path}")
            
    except Exception as e:
        print(f"FAILED with error: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())
