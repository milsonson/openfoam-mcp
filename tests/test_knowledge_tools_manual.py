import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.tools.knowledge_tools import openfoam_search_tutorials, SearchTutorialsInput


def test_knowledge_tools():
    result = openfoam_search_tutorials(SearchTutorialsInput(keywords=["simpleFoam"]))
    assert isinstance(result, str)
    assert result.strip() != ""
    assert ("未找到 OpenFOAM 教程目录" in result) or ("OpenFOAM 教程搜索结果" in result)

if __name__ == "__main__":
    test_knowledge_tools()
