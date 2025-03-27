# GameSage

## TODO
1. Platform crawler (Bilibili)  
2. Integrate LangChain or CrewAI (experiment)

## Progress

| **Main Task**     | **Category** | **Owner** | **Current Progress**                                  |
|-------------------|--------------|-----------|--------------------------------------------------------|
| Agent MVP         | Function     | wyx       | ![100%](https://progress-bar.xyz/100)                 |
| Bilibili Crawler  | Component    | qz        | ![33%](https://progress-bar.xyz/33)                   |
| LangChain Integration | Framework | wyx       | ![1%](https://progress-bar.xyz/1)                     |

## Tasks

- Crawler / Plugin
  - [ ] Bilibili - qz  
- Agent
  - [X] MVP - wyx  
  - [ ] Integrate ReAct structure - wyx  

## Usage

1. Get LLM API key from: https://cloud.siliconflow.cn/account/ak

## Notes

### MVP Extensions

1. Attempt LangChain or CrewAI integration  
2. Add more platform plugins (e.g., NGA)  
3. Enable multi-agent collaboration  
- Add a **query suggestion tool** to optimize search terms per platform  
  - Query expansion: e.g., when comparing WoW healers, AI adds related healing terms  

### Core Platforms (by importance)
1. Bilibili (comments, video ASR)  
2. Zhihu  
3. NGA  
4. Baidu Tieba  
5. GamerSky  
6. Xiaoheihe  
7. TapTap (mobile games)

---

## Overall Strategy: Agent-based Multi-turn Reasoning + Plugin-driven Platform Routing  
**(Hybrid ReAct-Plugin Strategy)**

---

### Main Objective:
To build an LLM-driven, plugin-based system for intelligent game guide retrieval, extraction, correction, and answering—across multiple platforms. It supports task decomposition and tool invocation, and can gradually evolve into a **multi-agent collaboration system**.

---

### I. System Architecture

```
+-----------------------------+
|        User Query           |
+-----------------------------+
              |
              v
+-----------------------------+
|     Main Agent Module       |   <- Core 1 (ReAct)
|   - Multi-turn reasoning    |
|   - Tool orchestration      |
+-----------------------------+
              |
              v
+-----------------------------+
|    Tool Selection & Calls   |
| - SearchTool                |
| - WebExtractor (ASR)        |
| - Summarizer                |
+-----------------------------+
              |
              v
+-----------------------------+
|   Plugin-based Platform     |   <- Core 2 (Platform Routing)
|   - Route query by intent   |
|   - Plugin modules per site |
+-----------------------------+
              |
              v
+-----------------------------+
|       Final Answer          |
+-----------------------------+
```

---

### II. Design Philosophy

#### 1. LLM as the Decision Core (Agent Controller)
- Uses ReAct-style prompts (Thought + Action + Action Input in JSON)
- Receives Observation from tool and continues reasoning
- Supports multi-step reasoning, retry, self-critique

#### 2. Tool Modules (Executors)
- **SearchTool**: DuckDuckGo/Serper API with `site:` keyword support  
- **WebExtractor**: Bilibili video-to-ASR (Whisper)  
- **ASRCorrector**: Fixes terminology errors in ASR results  
- **Summarizer**: Combines answers from multiple sources  

#### 3. Plugin-based Platform Adapter (Strategy 4)
- Each platform is implemented as a plugin: search + extraction logic  
- Agent can specify platform via `site:` keyword or game type  
- Plugins reuse common tools (e.g., WebExtractor for videos)  
- Supports fallback to generic search if plugin fails  

#### 4. Multi-agent Collaboration (Future)
- **Planner Agent**: decomposes tasks  
- **Executor Agent**: invokes tools  
- **Critic Agent** (optional): evaluates steps, suggests retries

---

### III. Technology Stack

| Module         | Recommendation                          |
|----------------|------------------------------------------|
| Agent Logic    | Custom Agent Loop → LangGraph/CrewAI later |
| LLM API        | OpenAI / SiliconFlow / Local LLM wrapper |
| Tool System    | Standardized `Tool.run(input)` classes  |
| Plugins        | Modular, e.g., `plugin_bilibili.py`     |
| Prompt Format  | JSON outputs to reduce hallucination     |

---

### IV. Advantages

| Method 2 (ReAct) | Method 4 (Plugins) | Hybrid Benefits |
|------------------|--------------------|------------------|
| LLM can reason   | Platform-specific logic | Intelligent + modular |
| Flexible tools   | Maintainable plugins | Low-cost extensibility |
| ReAct chaining   | Per-platform optimization | Evolves into multi-agent |

---

### V. Application Scenarios

- Game advice: class selection, gear optimization, mechanic breakdown  
- ASR noise-tolerant QA (especially with video content)  
- Multi-source content fusion (forums, videos, comments)  
- Open-source LLM demos, educational tools, multi-agent research

---

**Future Work Ideas**  
- Caching frequently asked queries  
- Query rewriter for vague questions  
- User persona adaptation (beginner vs veteran)  
- Tool invocation logging for research/debugging
