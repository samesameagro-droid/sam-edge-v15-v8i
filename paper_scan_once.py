from main_paper import PaperEngine

if __name__ == "__main__":
    print("STARTUP OK | V8I FROZEN | GITHUB ACTIONS ONE-SHOT | PAPER ONLY")
    eng = PaperEngine()
    try:
        eng.scan()
    except Exception as e:
        print(f"SCAN FATAL | {type(e).__name__}: {e}")
        raise
