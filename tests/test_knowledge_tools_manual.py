import asyncio
from src.tools.knowledge_tools import openfoam_search_tutorials, SearchTutorialsInput

def test_knowledge_tools():
    print("Testing openfoam_search_tutorials...")
    result = openfoam_search_tutorials(SearchTutorialsInput(keywords=["simpleFoam"]))
    print(result)
    
    # Check if the result contains the expected error message (since no FOAM_TUTORIALS)
    if "未找到 OpenFOAM 教程目录" in result:
        print("\n✅ Test Passed: Correctly handled missing environment.")
    else:
        print("\n❌ Test Failed: Unexpected output.")

if __name__ == "__main__":
    test_knowledge_tools()
