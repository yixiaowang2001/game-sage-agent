# GameSage

## 1 Process Structure

### 1.1 Data Flow Structure Diagram

```text
                 Query from user
                       |
                     Router
         ┌─────────────┼─────────────┐
         ↓             ↓             ↓
     ┌───────┐     ┌───────┐     ┌───────┐
     │Plugin1│     │Plugin2│ ... │PluginN│
     └───────┘     └───────┘     └───────┘
         ↓             ↓             ↓
 ┌────────────┐ ┌────────────┐ ┌────────────┐
 │Summarizer1 │ │Summarizer2 │ │SummarizerN │
 └────────────┘ └────────────┘ └────────────┘
         \             |             /
          \            |            /
           \           |           /
            └── Final Summarizer ──┘
                       |
                     Output
```

### 1.2 Module Description

- **Query from user**  
  Query requests from users.

- **Router (LLM Based)**  
  Uses Large Language Model (LLM) to format questions and decide which plugins need to be called to process the request.

- **Plugin1 ... PluginN (Code Based)**  
  Multiple plugins retrieve information according to routing instructions.

- **Summarizer1 ... SummarizerN (LLM Based)**  
  Information returned from each plugin is summarized by respective summarizers.

- **Final Summarizer (LLM Based)**  
  Aggregates summaries returned by various plugins to generate the final output.

- **Output**  
  Displays the final result to the user.

### 1.3 Process Key Points

- LLM formats questions and decides which plugins to call  
- Plugins are responsible for information retrieval  
- Multi-level summarizers are responsible for integrating information  
- Final unified output answers the user's request

## 2 How to Start

1. Install the requirements.txt environment.
2. Log in to SiliconFlow and register to get an API: https://www.siliconflow.com/en/home. Put your API key under `configs/credential.py`.
3. Run `utils/cookies_tool.py`, log in to your Bilibili account, and save cookies.
4. In `core/agent.py`, enter your questions in test_queries and run the program.

## 3 Notes

1. Currently, the project does not support custom APIs, so a SiliconFlow API is required.
2. Users need to have a Bilibili account.
3. Due to LLM translation issues, the accuracy for English queries and English output results may not be sufficient.
4. The project is currently being refactored, and the code will be gradually updated.

## 4 Code Structure

```
game-sage_backup
├── README.md
├── configs
│   ├── credential.py     
│   ├── global_config.py
│   ├── llm_config.py
│   ├── logger_config.py
│   ├── tool_config.py
│   └── utils_config.py
├── core
│   ├── __init__.py
│   ├── agent.py
│   ├── chains.py
│   └── llm.py
├── prompts
│   ├── __init__.py
│   ├── agent_prompts.py
│   ├── summarizer_prompts.py
│   └── utils_prompts.py
├── tools
│   ├── bilibili_tool.py
│   ├── nga_tool.py
│   └── tieba_tool.py
└── utils
    ├── bilibili_utils
    │   ├── comments_crawler.py
    │   ├── searcher.py
    │   └── video_info_extractor.py
    ├── cookies_tool.py
    └── parsers.py
```