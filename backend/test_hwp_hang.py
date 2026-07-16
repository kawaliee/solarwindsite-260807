import sys
import logging
from hwp_hwpx_parser import read
import traceback
import signal

logging.basicConfig(level=logging.DEBUG)

filepath = "/app/media/당진PJT(당진행복솔라)_1단계/계약서/EPC/제Ⅳ편 기술규격서_2023.10.23/제7장 설계 과업 지시서/제7-1장 설계 과업 지시서-발전단지.hwp"

class TimeoutException(Exception): pass
def handler(signum, frame):
    print("--- MAIN THREAD STACK TRACE AT TIMEOUT ---")
    traceback.print_stack(frame)
    raise TimeoutException("Timeout hit!")

signal.signal(signal.SIGALRM, handler)

def main():
    print(f"Testing parsing for {filepath}...")
    
    reader = None
    try:
        signal.alarm(5)
        print("Executing read(filepath)...")
        reader = read(filepath)
        print("read() successful.")
        
        signal.alarm(5)
        print("Executing reader.text...")
        text = reader.text
        print("reader.text successful. Length:", len(text) if text else 0)
        
    except TimeoutException:
        print("HANG DETECTED! Timeout Exception caught.")
    except Exception as e:
        print(f"Exception occurred: {e}")
        traceback.print_exc()
    finally:
        signal.alarm(0)
        if reader:
            try:
                reader.close()
            except:
                pass

if __name__ == "__main__":
    main()
