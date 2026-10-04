"""Recognize explicit action requests and refusals without guessing ambiguous intent."""
import re


ACTION_WORDS = {"human": r"人工|工程师|真人|human support|engineer|human agent", "recovery": r"重试|重发|恢复|修复|创建.{0,8}(?:计划|方案)|retry|resend|recover|repair|(?:create|propose).{0,12}(?:plan|action)"}
NEGATION = r"不要|不需要|不用|无需|不必|先别|先不|暂时不|别|禁止|暂缓|do not|don't|no need|without|hold off|not yet|never"
PAUSE = r"(?:暂时|目前|现在|先)?(?:不用|不需要|不要|暂停|暂缓)处理|(?:do not|don't|hold off).{0,8}(?:handle|process|act)"


def action_request(text: str, operation: str) -> bool | None:
    decision = None
    for sentence in re.split(r"[。.!?！？；;\n]|但是|不过|而是|\b(?:but|however)\b", text.replace("’", "'"), flags=re.IGNORECASE):
        conditional = False
        for clause in re.split(r"[，,]", sentence):
            if re.search(r"如果|假如|一旦|若|\b(?:if|unless|once)\b", clause, flags=re.IGNORECASE):
                conditional = True
            if re.match(r"\s*(?:现在|目前|直接|now|instead)", clause, flags=re.IGNORECASE):
                conditional = False
            if conditional:
                continue
            if re.search(PAUSE, clause, flags=re.IGNORECASE):
                decision = False
            for match in re.finditer(ACTION_WORDS[operation], clause, flags=re.IGNORECASE):
                before, after = clause[:match.start()], clause[match.end():]
                if re.search(r"\b(?:whether|why)\b|是否|为什么|会不会", before, flags=re.IGNORECASE):
                    continue
                if operation == "human" and re.match(r".{0,8}(?:是否|为什么|会不会|意味着什么|有何含义|\b(?:whether|why|what does)\b)", after, flags=re.IGNORECASE) and not re.search(r"请(?!求)|帮我|我要|我需要|\b(?:please|can you|i want|i need)\b", before, flags=re.IGNORECASE):
                    continue
                if re.search(r"(?:" + NEGATION + r").{0,12}$", before, flags=re.IGNORECASE) or re.match(r"\s*(?:暂时|现在|目前)?(?:不要|不用|不需要|无需|not needed)", after, flags=re.IGNORECASE):
                    decision = False
                elif re.search(r"请|帮我|麻烦|需要|我要|转|交给|找|联系|\b(?:please|need|want|send|escalate|connect|contact|ask|can you)\b", before, flags=re.IGNORECASE) or match.start() == 0:
                    decision = True
    return decision


def is_paused(text: str) -> bool:
    return bool(re.search(PAUSE, text, flags=re.IGNORECASE)) and not re.search(r"查|核对|解释|说明|诊断|inspect|check|diagnos|explain|investigat", text, flags=re.IGNORECASE) and action_request(text, "human") is not True and action_request(text, "recovery") is not True
