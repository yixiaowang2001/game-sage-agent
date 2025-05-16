import asyncio
import functools
import json
import logging
import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

from pydub import AudioSegment
import whisper

from core.llm import llm
from configs.utils_config import WHISPER_MODEL_SIZE, BILIBILI_VIDEO_TRANSCRIPT_LENGTH_LIMIT
from configs.global_config import CUDA_AVAILABLE
from configs.logger_config import get_logger
from prompts.utils_prompts import BILIBILI_CORRECT_TRANSCRIPT_PROMPT

TEMP_AUDIO_DIR = "temp_audio"
os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)
logger = get_logger("utils.bilibili_utils.transcript_extractor")


class BilibiliVideoInfoExtractor:
    def __init__(self, cookies=None):
        self.cookies = cookies

    async def _extract_audio(self, video_url, output_dir=TEMP_AUDIO_DIR):
        output_path = None
        duration = None
        description = None
        title = None
        tags = None
        bvid = None
        was_truncated = False
        try:
            cmd_parts = ["yt-dlp", "-f", "ba"]

            if self.cookies:
                cmd_parts.extend(["--add-header", f"Cookie:{self.cookies}"])

            if BILIBILI_VIDEO_TRANSCRIPT_LENGTH_LIMIT and BILIBILI_VIDEO_TRANSCRIPT_LENGTH_LIMIT > 0:
                limit_minutes = BILIBILI_VIDEO_TRANSCRIPT_LENGTH_LIMIT
                time_limit = f"*00:00:00-00:{limit_minutes:02d}:00"
                cmd_parts.extend(["--download-sections", time_limit])
                was_truncated = True
                logger.info(f"Limiting download to first {limit_minutes} minutes only")

            cmd_parts.append("--print-json")
            temp_filename = f"temp_audio_{os.urandom(8).hex()}"
            temp_output_template = os.path.join(output_dir, f"{temp_filename}.%(ext)s")
            cmd_parts.extend(["-o", temp_output_template])
            cmd_parts.append(video_url)
            cmd = cmd_parts
            
            logger.debug(f"Executing yt-dlp command for {video_url}: {' '.join(cmd)}")
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout_bytes, stderr_bytes = await process.communicate()

            stdout = stdout_bytes.decode(errors='ignore')
            stderr = stderr_bytes.decode(errors='ignore')

            json_line = None
            for line in reversed(stdout.strip().split('\n')):
                if line.startswith('{') and line.endswith('}'):
                    json_line = line
                    break

            info = None
            if json_line:
                try:
                    info = json.loads(json_line)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to decode yt-dlp JSON output: {json_line}")
            else:
                logger.warning(f"No JSON info line found in yt-dlp stdout for {video_url}")

            if stderr:
                log_level = logging.ERROR if process.returncode != 0 or not info else logging.DEBUG
                logger.log(log_level, f"yt-dlp stderr:\n{stderr}")

            if process.returncode != 0 or not info:
                logger.error(f"yt-dlp process failed (code: {process.returncode}) or info missing.")
                try:
                    for f in os.listdir(output_dir):
                        if f.startswith(temp_filename):
                            os.remove(os.path.join(output_dir, f))
                            logger.debug(f"Cleaned up temporary file: {f}")
                            break
                except Exception as cleanup_e:
                    logger.warning(f"Error during temp file cleanup: {cleanup_e}")
                return None, None, None, None, None, None, False

            bvid = info.get("id")
            title = info.get("title", "output").strip().replace("/", "_").replace("\\\\", "_")
            tags = info.get("tags", [])
            duration = info.get("duration")
            description = info.get("description")
            downloaded_filepath = info.get('_filename')

            if not downloaded_filepath or not os.path.exists(downloaded_filepath):
                logger.error(f"Output file path missing or file not found: {downloaded_filepath}")
                found_temp = None
                try:
                    for f in os.listdir(output_dir):
                        if f.startswith(temp_filename):
                            found_temp = os.path.join(output_dir, f)
                            logger.warning(f"Could not find reported path, using detected temp file: {found_temp}")
                            break
                except Exception as list_e:
                    logger.warning(f"Error listing dir during fallback check: {list_e}")

                if found_temp:
                    downloaded_filepath = found_temp
                else:
                    return None, title, tags, duration, description, bvid, False

            try:
                audio_ext = os.path.splitext(downloaded_filepath)[1]
                safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '_', '-')).rstrip()[:100]
                final_filename = f"{safe_title}{audio_ext}"
                output_path = os.path.join(output_dir, final_filename)

                if downloaded_filepath != output_path:
                    os.rename(downloaded_filepath, output_path)
                    logger.debug(f"Renamed downloaded file to: {output_path}")
                else:
                    output_path = downloaded_filepath
                    logger.debug(f"Downloaded file already has desired name: {output_path}")

            except Exception as rename_e:
                logger.warning(
                    f"Failed to rename downloaded file {downloaded_filepath}, using original path. Error: {rename_e}")
                output_path = downloaded_filepath

            return output_path, title, tags, duration, description, bvid, was_truncated

        except Exception as e:
            logger.exception(f"Unexpected error during audio extraction for {video_url}: {e}")
            path_to_clean = output_path or (locals().get('downloaded_filepath'))
            if path_to_clean and os.path.exists(path_to_clean):
                try:
                    os.remove(path_to_clean)
                    logger.debug(f"Cleaned up file on error: {path_to_clean}")
                except Exception as cleanup_e:
                    logger.warning(f"Error during cleanup: {cleanup_e}")
            try:
                if 'temp_filename' in locals():
                    for f in os.listdir(output_dir):
                        if f.startswith(temp_filename):
                            if not path_to_clean or os.path.abspath(os.path.join(output_dir, f)) != os.path.abspath(
                                    path_to_clean):
                                os.remove(os.path.join(output_dir, f))
                                logger.debug(f"Cleaned up leftover temp file: {f}")
            except Exception as cleanup_e:
                logger.warning(f"Error during temp file prefix cleanup: {cleanup_e}")

            return None, None, None, None, None, None, False

    def _convert_to_wav(self, input_path, trim_duration_ms=None):
        wav_path = None
        try:
            logger.debug(f"Converting {input_path} to WAV...")
            wav_path = os.path.splitext(input_path)[0] + ".wav"
            input_format = os.path.splitext(input_path)[1][1:]
            if not input_format:
                try:
                    from pydub.utils import mediainfo
                    info = mediainfo(input_path)
                    input_format = info.get('format_name')
                    if not input_format:
                        raise ValueError("Could not detect audio format")
                    logger.debug(f"Detected audio format: {input_format}")
                except Exception as e:
                    logger.warning(f"Could not detect audio format for {input_path}, assuming 'm4a'. Error: {e}")
                    input_format = "m4a"

            logger.debug(f"Loading audio file: {input_path} with format {input_format}")
            audio = AudioSegment.from_file(input_path, format=input_format)
            original_duration_sec = len(audio) / 1000.0
            logger.debug(f"Original audio duration: {original_duration_sec:.2f} seconds")

            os.makedirs(os.path.dirname(wav_path), exist_ok=True)
            logger.debug(f"Exporting audio to WAV: {wav_path}")
            audio.export(wav_path, format="wav")
            logger.debug(f"Successfully audio converted to {wav_path}")
            return wav_path
        except FileNotFoundError:
            logger.error(f"Error converting to WAV: Input file not found at {input_path}")
            return None
        except Exception as e:
            logger.exception(f"Error converting {input_path} to WAV: {e}")
            if wav_path and os.path.exists(wav_path) and input_path != wav_path:
                try:
                    os.remove(wav_path)
                    logger.debug(f"Cleaned up partially converted file: {wav_path}")
                except Exception as cleanup_e:
                    logger.warning(f"Error during conversion cleanup: {cleanup_e}")
            return None

    async def _transcribe_audio(self, audio_path):
        try:
            effective_model_size = WHISPER_MODEL_SIZE
            logger.debug(f"Submitting transcription task for {audio_path} using model {effective_model_size}")
            loop = asyncio.get_running_loop()
            with ProcessPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool,
                    functools.partial(transcribe_worker, audio_path, effective_model_size)
                )
            logger.debug(f"Transcription task completed for {audio_path}")
            return result
        except Exception as e:
            logger.exception(f"Error submitting/running transcription task for {audio_path}: {e}")
            return None

    async def _correct_transcript(
        self, 
        raw_text,
        video_title=None,
        tags=None,
        description=None
    ):
        if not raw_text: 
            return raw_text
        if llm is None:
            logger.error("LLM instance is not available. Skipping correction.")
            return raw_text 

        tag_string = ", ".join(tags) if tags else "None"
        desc_string = description if description else "None"
        title_string = video_title if video_title else "None"
        
        logger.debug(f"Requesting LLM correction for video: {title_string}...")
        
        try:
            prompt_value = BILIBILI_CORRECT_TRANSCRIPT_PROMPT.format_prompt(
                video_title=title_string,
                tag_string=tag_string,
                description=desc_string,
                raw_text=raw_text
            )
            
            response = await llm.ainvoke(prompt_value)
            corrected_text = response.content 
            logger.debug(f"LLM correction finished for video: {title_string}.")
            return corrected_text
        except Exception as e:
            logger.exception(f"Error during LLM correction: {e}")
            return raw_text 

    async def get_video_info(self, video_url, llm_correction=True):
        result = {
            'bvid': None,
            'title': None,
            'description': None,
            'tags': None,
            'duration': None,
            'transcript': None,
            'error': None
        }

        audio_path = None
        wav_path = None

        try:
            logger.debug(f"Attempting to extract audio and metadata from: {video_url}")
            audio_path, title, tags, duration, description, bvid, was_truncated = await self._extract_audio(video_url)

            result['bvid'] = bvid
            result['title'] = title
            result['tags'] = tags
            result['duration'] = duration
            result['description'] = description

            if not audio_path:
                error_msg = f"Error: Failed to download or find audio for {video_url}."
                if title:
                    error_msg += f" (Title: {title})"
                logger.error(error_msg)
                result['error'] = error_msg
                return result

            log_msg = f"Audio extracted successfully: {audio_path} (Title: {title}"
            if duration:
                log_msg += f", Duration: {duration:.2f}s"
            else:
                log_msg += ", Duration: Unknown"
            if description:
                desc_preview = description[:50].replace('\n', ' ') + ('...' if len(description) > 50 else '')
                log_msg += f", Desc: '{desc_preview}'"
            log_msg += ")"
            logger.info(log_msg)

            logger.info(f"Converting {os.path.basename(audio_path)} to WAV...")
            wav_path = await asyncio.get_running_loop().run_in_executor(
                ThreadPoolExecutor(),
                self._convert_to_wav,
                audio_path,
                None
            )

            if not wav_path:
                error_msg = f"Error: Failed to convert {audio_path} to WAV."
                logger.error(error_msg)
                result['error'] = error_msg
                return result

            logger.info(f"Conversion successful: {wav_path}")

            logger.info(f"Starting transcription for {os.path.basename(wav_path)}...")
            transcript_text = await self._transcribe_audio(wav_path)

            if transcript_text is None:
                error_msg = f"Error: Transcription failed for {wav_path}."
                logger.error(error_msg)
                result['error'] = error_msg
                return result
            elif not transcript_text:
                logger.warning(f"Transcription resulted in empty text for {wav_path}.")
                transcript_text = ""

            transcript_head = f"（截断前{BILIBILI_VIDEO_TRANSCRIPT_LENGTH_LIMIT}分钟）" if was_truncated else ""

            result['transcript'] = transcript_head + transcript_text
            logger.info(f"Transcription successful for {os.path.basename(wav_path)}.")

            if llm_correction:
                logger.info("Applying LLM correction")
                corrected_text = await self._correct_transcript(
                    transcript_text,
                    video_title=title,
                    tags=tags,
                    description=description
                )
                if corrected_text is not None and corrected_text != transcript_text:
                    result['transcript'] = transcript_head + corrected_text
                    logger.info("LLM correction applied successfully.")
                elif corrected_text == transcript_text:
                    logger.info("LLM correction resulted in no changes.")
                else:
                    logger.warning("LLM correction failed, using raw transcript.")
            else:
                logger.info("Skipping LLM correction.")

            logger.info(f"Finished processing {video_url}. Returning results.")
            return result

        except Exception as e:
            logger.exception(f"An unexpected error occurred in get_video_info for {video_url}: {e}")
            result['error'] = f"Unexpected error: {e}"
            return result

        finally:
            logger.debug("Performing cleanup...")
            files_to_remove = [audio_path, wav_path]
            for f in files_to_remove:
                if f and os.path.exists(f):
                    try:
                        os.remove(f)
                        logger.debug(f"Removed temporary file: {f}")
                    except Exception as e:
                        logger.warning(f"Failed to remove temporary file {f}: {e}")
            try:
                if os.path.exists(TEMP_AUDIO_DIR) and not os.listdir(TEMP_AUDIO_DIR):
                    os.rmdir(TEMP_AUDIO_DIR)
                    logger.debug(f"Removed empty temporary directory: {TEMP_AUDIO_DIR}")
            except OSError as e:
                logger.warning(f"Could not remove temporary directory {TEMP_AUDIO_DIR}: {e}")
            except Exception as e:
                logger.warning(f"Unexpected error during temporary directory cleanup: {e}")


def transcribe_worker(audio_path: str, model_size: str):
    try:
        logger.debug(f"[Worker {os.getpid()}] Loading Whisper model: {model_size}...")
        model = whisper.load_model(model_size, device="cuda" if CUDA_AVAILABLE else "cpu")
        logger.debug(f"[Worker {os.getpid()}] Starting transcription for {os.path.basename(audio_path)}...")
        result = model.transcribe(audio_path, language="zh", fp16=CUDA_AVAILABLE)
        logger.debug(f"[Worker {os.getpid()}] Transcription finished.")
        return result["text"]
    except Exception as e:
        logger.error(f"[Worker {os.getpid()}] Error during transcription: {e}")
        return None


if __name__ == "__main__":
    from utils.cookies_tool import load_cookies
    import pprint

    test_url = "https://www.bilibili.com/video/BV1gcoeY8Eq9"

    cookies = load_cookies("Bilibili")
    extractor = BilibiliVideoInfoExtractor(cookies=cookies)

    video_info = asyncio.run(extractor.get_video_info(
        video_url=test_url,
        llm_correction=False
    ))

    pprint.pprint(video_info)