# Pyzirek Desktop: окно с сайтом Pyzirek AI + Агент, который управляет компьютером.
import base64, ctypes, io, json, os, sys, threading, time, urllib.error, urllib.request

SITE = "https://pyzirekai.minecraftfrindef.workers.dev/"
MAX_STEPS = 30
SHOT_WIDTH = 1280

try:  # координаты экрана должны совпадать с физическими пикселями
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import pyautogui
import pyperclip
import webview

pyautogui.FAILSAFE = True   # резко уведите мышь в угол экрана, чтобы остановить
pyautogui.PAUSE = 0.15
WINDOW = None

KEYMAP = {"control": "ctrl", "return": "enter", "esc": "esc", "escape": "esc", "del": "delete",
          "windows": "win", "super": "win", "cmd": "win", "arrowup": "up", "arrowdown": "down",
          "arrowleft": "left", "arrowright": "right", "pgup": "pageup", "pgdn": "pagedown"}


class Api:
    def __init__(self):
        self._lock = threading.Lock()
        self._lines = []
        self._running = False
        self._stop = threading.Event()

    # ---- методы, которые вызывает сайт ----
    def agent_start(self, goal, token, api, model="auto"):
        if self._running:
            return {"error": "Агент уже работает"}
        goal = str(goal or "").strip()
        if not goal:
            return {"error": "Опишите задачу"}
        ok = WINDOW.create_confirmation_dialog(
            "Pyzirek Агент",
            "Агент возьмёт под контроль мышь и клавиатуру и будет выполнять задачу:\n\n" + goal[:300] +
            "\n\nОстановить можно кнопкой СТОП или уводом мыши в угол экрана. Начать?")
        if not ok:
            return {"error": "Отменено"}
        self._stop.clear()
        self._running = True
        with self._lock:
            self._lines = []
        threading.Thread(target=self._overlay, daemon=True).start()
        threading.Thread(target=self._run, args=(goal, str(token), str(api), str(model or "auto")), daemon=True).start()
        return {"ok": True}

    def agent_stop(self):
        self._stop.set()
        return True

    def agent_poll(self):
        with self._lock:
            lines, self._lines = self._lines, []
        return {"lines": lines, "running": self._running}

    # ---- внутреннее ----
    def _log(self, text):
        with self._lock:
            self._lines.append(text)

    def _overlay(self):
        try:
            import tkinter as tk
            root = tk.Tk()
            root.title("Pyzirek Агент")
            root.attributes("-topmost", True)
            root.geometry("230x84+20+20")
            root.resizable(False, False)
            tk.Label(root, text="Агент управляет компьютером", font=("Segoe UI", 9)).pack(pady=(6, 2))
            tk.Button(root, text="СТОП", bg="#c0392b", fg="white", font=("Segoe UI", 12, "bold"),
                      command=self._stop.set).pack(fill="x", padx=10, pady=4)

            def tick():
                if not self._running:
                    root.destroy()
                else:
                    root.after(300, tick)
            root.after(300, tick)
            root.mainloop()
        except Exception:
            pass

    @staticmethod
    def _grid(img):
        # красная сетка с подписями 100..900: по ней модель точнее указывает координаты
        from PIL import Image, ImageDraw
        w, h = img.size
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        for i in range(1, 10):
            x, y = int(w * i / 10), int(h * i / 10)
            d.line([(x, 0), (x, h)], fill=(255, 0, 0, 90), width=1)
            d.line([(0, y), (w, y)], fill=(255, 0, 0, 90), width=1)
            d.text((x + 3, 3), str(i * 100), fill=(255, 0, 0, 255))
            d.text((3, y + 3), str(i * 100), fill=(255, 0, 0, 255))
        return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")

    def _shot(self):
        img = pyautogui.screenshot().convert("RGB")
        sw, sh = img.size
        k = min(1.0, SHOT_WIDTH / sw)
        w, h = int(sw * k), int(sh * k)
        if k < 1:
            img = img.resize((w, h))
        img = self._grid(img)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=70)
        return base64.b64encode(buf.getvalue()).decode(), w, h, sw, sh

    def _post(self, api, token, body):
        req = urllib.request.Request(api.rstrip("/") + "/agent", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + token,
                                              "User-Agent": "PyzirekDesktop/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read().decode()).get("error")
            except Exception:
                msg = None
            raise RuntimeError(msg or ("Ошибка сервера " + str(e.code)))

    def _run(self, goal, token, api, model):
        history, last, repeats = [], None, 0
        try:
            self._log("Начинаю: " + goal)
            for n in range(1, MAX_STEPS + 1):
                if self._stop.is_set():
                    self._log("Остановлено пользователем")
                    return
                img, w, h, sw, sh = self._shot()
                try:
                    res = self._post(api, token, {"goal": goal, "image": img, "w": w, "h": h, "history": history, "model": model})
                except Exception as e:
                    self._log("Ошибка: " + str(e))
                    return
                step = res.get("step") or {}
                act = str(step.get("action", "wait")).lower()
                say = str(step.get("say", ""))
                norm = bool(res.get("norm"))
                if n == 1 and res.get("model"):
                    self._log("Модель: " + str(res["model"]))

                def px(v, size, shot):
                    v = float(v or 0)
                    return int(max(0, min(size - 1, v / 1000 * size if norm else v / shot * size)))
                x, y = px(step.get("x"), sw, w), px(step.get("y"), sh, h)
                self._log("%d. %s %s" % (n, act, say))
                sig = (act, x // 20, y // 20, str(step.get("text", "")), str(step.get("keys", "")))
                repeats = repeats + 1 if sig == last else 0
                last = sig
                if repeats >= 3 and act != "wait":
                    self._log("Агент повторяет одно и то же. Останавливаю.")
                    return
                if act == "done":
                    self._log("Готово.")
                    return
                self._act(act, step, x, y)
                history.append("%d. %s %s" % (n, act, say))
                time.sleep(1.0)
            self._log("Достигнут лимит шагов (%d)" % MAX_STEPS)
        except pyautogui.FailSafeException:
            self._log("Остановлено: мышь уведена в угол экрана")
        except Exception as e:
            self._log("Ошибка: %s" % e)
        finally:
            self._running = False

    def _act(self, act, step, x, y):
        if act == "click":
            pyautogui.click(x, y)
        elif act == "double_click":
            pyautogui.doubleClick(x, y)
        elif act == "right_click":
            pyautogui.rightClick(x, y)
        elif act == "type":
            text = str(step.get("text", ""))
            if text.isascii():
                pyautogui.write(text, interval=0.02)
            else:  # кириллица и эмодзи: через буфер обмена
                old = None
                try:
                    old = pyperclip.paste()
                except Exception:
                    pass
                pyperclip.copy(text)
                pyautogui.hotkey("ctrl", "v")
                time.sleep(0.3)
                if old is not None:
                    try:
                        pyperclip.copy(old)
                    except Exception:
                        pass
        elif act == "key":
            keys = [KEYMAP.get(k, k) for k in str(step.get("keys", "")).lower().replace(" ", "").split("+") if k]
            if len(keys) == 1:
                pyautogui.press(keys[0])
            elif keys:
                pyautogui.hotkey(*keys)
        elif act == "scroll":
            if x or y:
                pyautogui.moveTo(x, y)
            pyautogui.scroll(int(step.get("amount", -5) or -5) * 100)
        else:  # wait и всё неизвестное
            time.sleep(1.5)


def main():
    global WINDOW
    api = Api()
    WINDOW = webview.create_window("Pyzirek AI", SITE, js_api=api, width=1280, height=820, min_size=(900, 600))
    storage = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "PyzirekAI")
    os.makedirs(storage, exist_ok=True)
    webview.start(private_mode=False, storage_path=storage)


if __name__ == "__main__":
    main()
