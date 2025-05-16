from langchain_openai import ChatOpenAI

from configs.logger_config import get_logger
from configs.llm_config import MODEL_NAME, BASE_URL
from configs.credential import SILICONFLOW_API

logger = get_logger("core.llm")


try:
    llm = ChatOpenAI(
        model=MODEL_NAME,
        openai_api_key=SILICONFLOW_API,
        openai_api_base=BASE_URL,
        temperature=0.7,
        max_tokens=2048,
        top_p=0.9,
    )
    logger.debug(f"LangChain ChatOpenAI LLM instance created successfully for model: {MODEL_NAME}")
except Exception as e:
    logger.error(f"Error creating LangChain LLM instance for: {e}")
    llm = None
