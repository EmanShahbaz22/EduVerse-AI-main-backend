import httpx
import asyncio

BASE_URL = "http://127.0.0.1:8008"
PREFIX = "/adaptive"

# Valid IDs found in the database
STUDENT_ID = "694ee008b1d153952b58a86c"
QUIZ_ID = "6975fecd5e46d8c3a2b664d7"
COURSE_ID = "6975fb485e46d8c3a2b664d6"

async def test_classification():
    print("\n--- Testing /adaptive/classify ---")
    payload = {
        "courseId": COURSE_ID,
        "quizId": QUIZ_ID,
        "scorePercentage": 85.0,
        "timeSpentSeconds": 120,
        "timeLimitSeconds": 300
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(f"{BASE_URL}{PREFIX}/classify/{STUDENT_ID}", json=payload)
            print(f"Status: {response.status_code}")
            if response.status_code == 200:
                print(f"Success! Classification: {response.json().get('pace')}")
            else:
                print(f"Error: {response.text}")
        except Exception as e:
            print(f"Failed to connect: {e}")

async def test_generate_lesson():
    print("\n--- Testing /adaptive/generate-lesson ---")
    payload = {
        "courseId": COURSE_ID,
        "quizId": QUIZ_ID,
        "topic": "Mathematics - Calculus",
        "weakAreas": "Integration by parts"
    }
    async with httpx.AsyncClient() as client:
        try:
            # Note: generate-lesson takes student_id as a query param in the endpoint code
            # Actually, looking at routers/adaptive_learning.py, student_id is a path param? 
            # No, it's: async def generate_lesson_endpoint(student_id: str, request: LessonGenerationRequest, ...)
            # So it's a query parameter if not in the path.
            # Let's check the router prefix again. router = APIRouter(prefix="/adaptive")
            # @router.post("/generate-lesson")
            # This means student_id MUST be a query param.
            
            response = await client.post(
                f"{BASE_URL}{PREFIX}/generate-lesson", 
                params={"student_id": STUDENT_ID}, 
                json=payload,
                timeout=60.0  # AI generation can take a while
            )
            print(f"Status: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                print("Success! AI Lesson Generated.")
                print(f"Lesson Title: {data['lesson']['title']}")
                print(f"Classification: {data['classification']['pace']}")
            else:
                print(f"Error: {response.text}")
        except Exception as e:
            print(f"Failed to connect: {e}")

async def test_openapi_schema():
    print("\n--- Testing /openapi.json ---")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f"{BASE_URL}/openapi.json")
            print(f"Status: {response.status_code}")
            if response.status_code == 200:
                print("Success! OpenAPI schema is valid.")
            else:
                print(f"Error: {response.text}")
        except Exception as e:
            print(f"Failed to connect: {e}")

if __name__ == "__main__":
    asyncio.run(test_openapi_schema())
    asyncio.run(test_classification())
    asyncio.run(test_generate_lesson())
