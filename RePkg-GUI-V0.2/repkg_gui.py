# -*- coding: utf-8 -*-
"""RePKG 提取工具（tkinter 图形界面）

支持 Wallpaper Engine 的 .pkg / .mpkg：
    .pkg  -> 直接交给同目录的 RePKG.exe extract
    .mpkg -> 建一个临时 .pkg 链接/副本再交给 RePKG（RePKG 只按扩展名识别，
             已实测 PKGM 包头可正常解析），结束后自动删除临时文件。

其余功能：
    选择输出文件夹、后台线程调用、日志实时显示 stdout/stderr、
    中文/空格路径安全、自动生成默认输出目录、一键打开输出文件夹。

RePKG.exe 查找顺序：程序同目录 -> 同目录 repkg.exe（可在 REPKG_EXE 追加兜底路径）。
"""

import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

# 可选：额外兜底路径；默认留空，程序优先使用同目录的 RePKG.exe
REPKG_EXE = ""

# 支持的包扩展名
PKG_EXTS = (".pkg", ".mpkg")

# 打包成 -w 程序后不弹出子进程的黑窗
CREATE_NO_WINDOW = 0x08000000

# 日志刷新间隔（毫秒）
POLL_MS = 100


def decode_line(raw):
    """按行解码子进程输出：优先 UTF-8，失败再用 GBK，最后忽略错误。"""
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def quote_for_log(arg):
    """仅用于日志展示，真实执行时用参数列表，不经过 shell。"""
    return '"{}"'.format(arg)


def split_chunk(buf):
    """把字节缓冲按 \\n / \\r 拆成整行，返回 (行列表, 剩余缓冲)。

    按 ASCII 换行符切分，对 UTF-8 和 GBK 都是安全的（换行字节不会出现在多字节字符内部）。
    """
    lines = []
    start = 0
    index = 0
    size = len(buf)
    while index < size:
        byte = buf[index]
        if byte == 0x0A:  # \n
            lines.append(buf[start:index])
            start = index + 1
        elif byte == 0x0D:  # \r
            lines.append(buf[start:index])
            if index + 1 < size and buf[index + 1] == 0x0A:
                index += 1
            start = index + 1
        index += 1
    return lines, buf[start:]


def resource_path(relative):
    """兼容源码运行和 PyInstaller 单文件运行，返回资源绝对路径。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


def app_dir():
    """程序所在目录（打包后是 exe 所在目录，源码运行时是脚本目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def repkg_candidates():
    """按优先级返回 RePKG.exe 的查找位置：优先同目录。"""
    base = app_dir()
    candidates = [
        os.path.join(base, "RePKG.exe"),
        os.path.join(base, "repkg.exe"),
    ]
    if REPKG_EXE:
        candidates.append(REPKG_EXE)
    return candidates


def resolve_repkg():
    for path in repkg_candidates():
        if os.path.isfile(path):
            return path
    return repkg_candidates()[0]


def read_package_magic(path):
    """读取 WE 包头的 magic（[i32 长度][magic]），失败返回空字节。"""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError:
        return b""
    if len(head) < 12:
        return b""
    return head[4:12]


def prepare_mpkg(pkg_path):
    """把 .mpkg 变成 RePKG 能识别的临时 .pkg。

    同盘优先硬链接（瞬间完成、不复制数据），跨盘时退回复制。
    返回 (临时 pkg 路径, 临时目录, 说明)；临时目录需要调用方清理。
    """
    tempdir = tempfile.mkdtemp(prefix="repkg_mpkg_")
    target = os.path.join(tempdir, "input.pkg")
    try:
        os.link(pkg_path, target)
        note = "mpkg 已用硬链接挂成临时 .pkg（不占额外空间）"
    except OSError:
        shutil.copyfile(pkg_path, target)
        note = "mpkg 已复制为临时 .pkg（RePKG 只按扩展名识别）"
    return target, tempdir, note


class RePkgApp(object):
    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.proc = None
        self.running = False
        self.last_out = ""
        self._auto_out = ""

        self.pkg_var = tk.StringVar()
        self.out_var = tk.StringVar()
        self.status_var = tk.StringVar(value="就绪：选择 .pkg / .mpkg 后点“开始提取”")

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.report_callback_exception = self._report_callback_exception
        self.root.after(POLL_MS, self._poll_queue)

    def _report_callback_exception(self, exc, value, tb):
        """-w 打包后没有控制台，界面回调异常改为写日志，避免静默失败。"""
        text = "".join(traceback.format_exception(exc, value, tb))
        self._append_log("[界面异常]\n" + text.rstrip())
        self._set_status("发生界面异常，详见日志")

    # ---------- 界面 ----------

    def _build_ui(self):
        self.root.title("RePKG 提取工具 - Wallpaper Engine pkg/mpkg")
        self.root.geometry("1380x800")
        self.root.resizable(False, False)
        self.root.configure(bg="#ffffff")

        # 右侧人物背景，左侧白色面板放控件
        self.bg_image = None
        bg_path = resource_path(os.path.join("assets", "bg_right.png"))
        if os.path.isfile(bg_path):
            try:
                self.bg_image = tk.PhotoImage(file=bg_path)
                tk.Label(self.root, image=self.bg_image, bd=0, bg="#ffffff").place(
                    x=560, y=0
                )
            except tk.TclError:
                self.bg_image = None

        left = tk.Frame(self.root, bg="#ffffff", width=560, height=800)
        left.place(x=0, y=0)
        left.pack_propagate(False)

        inner = tk.Frame(left, bg="#ffffff")
        inner.pack(fill="both", expand=True, padx=34)
        inner.columnconfigure(0, weight=1)
        inner.rowconfigure(9, weight=1)

        tk.Label(
            inner,
            text="RePKG 提取工具",
            bg="#ffffff",
            fg="#1c1c1e",
            font=("Microsoft YaHei UI", 17, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(34, 0))
        tk.Label(
            inner,
            text="Wallpaper Engine · PKG / MPKG 解包",
            bg="#ffffff",
            fg="#8c8c8c",
            font=("Microsoft YaHei UI", 9),
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        tk.Frame(inner, bg="#e8dcc8", height=1).grid(
            row=2, column=0, sticky="ew", pady=(16, 18)
        )

        label_style = {
            "bg": "#ffffff",
            "fg": "#6b6b6b",
            "font": ("Microsoft YaHei UI", 9),
            "anchor": "w",
        }

        def small_button(parent, text, command):
            return tk.Button(
                parent,
                text=text,
                command=command,
                font=("Microsoft YaHei UI", 9),
                bg="#ffffff",
                fg="#4a4a4a",
                activebackground="#f6efe4",
                activeforeground="#1c1c1e",
                relief="solid",
                bd=1,
                padx=10,
                pady=4,
                cursor="hand2",
                highlightthickness=0,
            )

        tk.Label(inner, text="包文件（.pkg / .mpkg）", **label_style).grid(
            row=3, column=0, sticky="w", pady=(0, 4)
        )
        pkg_row = tk.Frame(inner, bg="#ffffff")
        pkg_row.grid(row=4, column=0, sticky="ew")
        pkg_row.columnconfigure(0, weight=1)
        tk.Entry(
            pkg_row,
            textvariable=self.pkg_var,
            font=("Microsoft YaHei UI", 10),
            bg="#ffffff",
            fg="#222222",
            insertbackground="#222222",
            relief="solid",
            bd=1,
            highlightthickness=0,
        ).grid(row=0, column=0, sticky="ew", ipady=6)
        small_button(pkg_row, "选择...", self.choose_pkg).grid(
            row=0, column=1, sticky="ns", padx=(8, 0)
        )

        tk.Label(inner, text="输出文件夹", **label_style).grid(
            row=5, column=0, sticky="w", pady=(14, 4)
        )
        out_row = tk.Frame(inner, bg="#ffffff")
        out_row.grid(row=6, column=0, sticky="ew")
        out_row.columnconfigure(0, weight=1)
        tk.Entry(
            out_row,
            textvariable=self.out_var,
            font=("Microsoft YaHei UI", 10),
            bg="#ffffff",
            fg="#222222",
            insertbackground="#222222",
            relief="solid",
            bd=1,
            highlightthickness=0,
        ).grid(row=0, column=0, sticky="ew", ipady=6)
        small_button(out_row, "选择...", self.choose_out).grid(
            row=0, column=1, sticky="ns", padx=(8, 0)
        )

        self.start_btn = tk.Button(
            inner,
            text="开始提取",
            command=self.start,
            font=("Microsoft YaHei UI", 11, "bold"),
            bg="#1f1f21",
            fg="#ffffff",
            activebackground="#000000",
            activeforeground="#ffffff",
            disabledforeground="#bfbfbf",
            relief="flat",
            bd=0,
            pady=9,
            cursor="hand2",
        )
        self.start_btn.grid(row=7, column=0, sticky="ew", pady=(20, 8))

        sec = tk.Frame(inner, bg="#ffffff")
        sec.grid(row=8, column=0, sticky="w", pady=(0, 16))
        small_button(sec, "打开输出文件夹", self.open_out).pack(side="left")
        small_button(sec, "清空日志", self.clear_log).pack(side="left", padx=(8, 0))

        log_box = tk.Frame(
            inner, bg="#ffffff", highlightthickness=1, highlightbackground="#e6dccb"
        )
        log_box.grid(row=9, column=0, sticky="nsew")
        log_box.rowconfigure(0, weight=1)
        log_box.columnconfigure(0, weight=1)
        self.log = scrolledtext.ScrolledText(
            log_box,
            height=12,
            wrap="word",
            font=("Microsoft YaHei UI", 9),
            bg="#fffdf9",
            fg="#333333",
            relief="flat",
            bd=0,
            padx=8,
            pady=6,
            insertbackground="#333333",
        )
        self.log.grid(row=0, column=0, sticky="nsew")
        self.log.configure(state="disabled")

        tk.Label(
            inner,
            textvariable=self.status_var,
            bg="#ffffff",
            fg="#8c8c8c",
            font=("Microsoft YaHei UI", 9),
            anchor="w",
        ).grid(row=10, column=0, sticky="ew", pady=(8, 16))

    def _append_log(self, text):
        """往日志框追加一行（或一段）文本；text 为空字符串时插入空行。"""
        if text is None:
            return
        try:
            self.log.configure(state="normal")
            self.log.insert("end", text + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        except tk.TclError:
            pass

    def _set_status(self, text):
        try:
            self.status_var.set(text)
        except tk.TclError:
            pass

    def clear_log(self):
        try:
            self.log.configure(state="normal")
            self.log.delete("1.0", "end")
            self.log.configure(state="disabled")
        except tk.TclError:
            pass

    # ---------- 选择路径 ----------

    def choose_pkg(self):
        current = self._entry_path(self.pkg_var)
        initial = os.path.dirname(current) if current else os.getcwd()
        path = filedialog.askopenfilename(
            title="选择 Wallpaper Engine 包",
            initialdir=initial,
            filetypes=[
                ("Wallpaper Engine 包", "*.pkg *.mpkg"),
                ("PKG 文件", "*.pkg"),
                ("MPKG 文件", "*.mpkg"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        path = os.path.normpath(path)
        self.pkg_var.set(path)

        # 输出目录为空或仍是上次自动填的，就自动给出一个安全的默认目录
        if not self._entry_path(self.out_var) or self.out_var.get() == self._auto_out:
            stem = os.path.splitext(os.path.basename(path))[0]
            self._auto_out = os.path.join(os.path.dirname(path), stem + "_extracted")
            self.out_var.set(self._auto_out)

    def choose_out(self):
        current = self._entry_path(self.out_var)
        initial = current if os.path.isdir(current) else os.getcwd()
        path = filedialog.askdirectory(
            title="选择输出文件夹", initialdir=initial, mustexist=False
        )
        if path:
            self.out_var.set(os.path.normpath(path))

    @staticmethod
    def _entry_path(var):
        """去掉首尾空白和误粘贴进来的引号。"""
        return var.get().strip().strip('"').strip()

    def open_out(self):
        path = self._entry_path(self.out_var) or self.last_out
        if not path or not os.path.isdir(path):
            messagebox.showwarning("提示", "输出文件夹还不存在，请先提取。")
            return
        try:
            os.startfile(path)  # noqa: S606 - 打开用户自己选择的目录
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("错误", "无法打开文件夹：\n{}\n\n{}".format(path, exc))

    # ---------- 开始提取 ----------

    def start(self):
        if self.running:
            return

        repkg = resolve_repkg()
        if not os.path.isfile(repkg):
            messagebox.showerror(
                "错误",
                "找不到 RePKG.exe。\n"
                "请把 RePKG.exe 和本程序放在同一个文件夹后再运行。\n\n"
                "已查找：\n" + "\n".join(repkg_candidates()),
            )
            return

        pkg = self._entry_path(self.pkg_var)
        if not pkg:
            messagebox.showwarning("提示", "请先选择 .pkg 或 .mpkg 文件。")
            return
        if not os.path.isfile(pkg):
            messagebox.showerror("错误", "文件不存在：\n{}".format(pkg))
            return

        ext = os.path.splitext(pkg)[1].lower()
        if ext not in PKG_EXTS:
            messagebox.showerror(
                "错误", "只支持 .pkg 和 .mpkg 文件：\n{}".format(os.path.basename(pkg))
            )
            return

        magic = read_package_magic(pkg)
        if not magic.startswith(b"PKG"):
            messagebox.showerror(
                "错误",
                "这个文件不像 Wallpaper Engine 包（包头是 {!r}）。\n"
                "请确认选的是 .pkg 或 .mpkg 文件。".format(magic),
            )
            return

        out = self._entry_path(self.out_var)
        if not out:
            messagebox.showwarning("提示", "请先选择输出文件夹。")
            return
        out_abs = os.path.abspath(out)
        if self._is_unsafe_output(out_abs):
            messagebox.showerror(
                "错误",
                "不能把输出目录设为磁盘根目录或系统目录：\n{}".format(out_abs),
            )
            return
        if os.path.normcase(out_abs) == os.path.normcase(os.path.abspath(pkg)):
            messagebox.showerror("错误", "输出目录不能和输入文件相同。")
            return

        try:
            os.makedirs(out_abs, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(
                "错误", "无法创建输出文件夹：\n{}\n\n{}".format(out_abs, exc)
            )
            return

        try:
            with os.scandir(out_abs) as entries:
                non_empty = any(True for _ in entries)
        except OSError:
            non_empty = False
        if non_empty and not messagebox.askyesno(
            "确认",
            "输出文件夹不是空的：\n{}\n\nRePKG 默认不覆盖同名文件，继续吗？".format(out_abs),
        ):
            return

        self.last_out = out_abs
        self._append_log("")
        self._append_log("[{}] 开始提取".format(time.strftime("%H:%M:%S")))
        self._append_log("源文件：{}".format(pkg))
        self._append_log("输出到：{}".format(out_abs))
        if ext == ".mpkg":
            self._append_log("提示：mpkg 会先转成临时 .pkg 再交给 RePKG。")

        self.running = True
        self.start_btn.configure(state="disabled")
        self._set_status("正在准备…")

        worker = threading.Thread(target=self._run, args=(pkg, out_abs, repkg))
        worker.daemon = True
        worker.start()

    @staticmethod
    def _is_unsafe_output(path):
        drive, tail = os.path.splitdrive(os.path.abspath(path))
        if not tail.strip("\\/"):
            return True  # 磁盘根目录
        norm = os.path.normcase(os.path.abspath(path)).rstrip("\\/")
        forbidden = []
        for env in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)"):
            value = os.environ.get(env)
            if value:
                forbidden.append(os.path.normcase(value).rstrip("\\/"))
        return norm in forbidden

    def _run(self, pkg_path, out_dir, repkg):
        """后台线程：准备输入（mpkg 转换）、启动 RePKG、逐行读取输出。"""
        tempdir = None
        try:
            try:
                if pkg_path.lower().endswith(".mpkg"):
                    effective, tempdir, note = prepare_mpkg(pkg_path)
                    self.queue.put(("line", note))
                else:
                    effective = pkg_path
            except Exception as exc:  # noqa: BLE001
                self.queue.put(("error", "准备 mpkg 失败：{}".format(exc)))
                self.queue.put(("done", None))
                return

            args = [repkg, "extract", effective, "-o", out_dir]
            self.queue.put(("line", "命令：" + " ".join(quote_for_log(a) for a in args)))
            self.queue.put(("line", ""))

            try:
                proc = subprocess.Popen(
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=0,
                    creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except Exception as exc:  # noqa: BLE001
                self.queue.put(("error", "无法启动 RePKG：{}".format(exc)))
                self.queue.put(("done", None))
                return

            self.proc = proc
            code = -1
            try:
                self._pump(proc.stdout)
                code = proc.wait()
            except Exception as exc:  # noqa: BLE001
                self.queue.put(("error", "读取 RePKG 输出出错：{}".format(exc)))
                try:
                    code = proc.wait()
                except Exception:
                    code = -1
            finally:
                self.proc = None
                self.queue.put(("done", code))
        finally:
            if tempdir:
                shutil.rmtree(tempdir, ignore_errors=True)

    def _pump(self, pipe):
        """按块读取管道，按行解码后塞进队列，保证界面实时刷新。"""
        buf = b""
        try:
            while True:
                chunk = pipe.read(4096)
                if not chunk:
                    break
                buf += chunk
                lines, buf = split_chunk(buf)
                for raw in lines:
                    self._emit(raw)
            if buf:
                self._emit(buf)
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    def _emit(self, raw):
        text = decode_line(raw).rstrip()
        if text:
            self.queue.put(("line", text))

    # ---------- 队列轮询 / 收尾 ----------

    def _poll_queue(self):
        try:
            if not self.root.winfo_exists():
                return
        except tk.TclError:
            return

        lines = []
        done = None
        error = None
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "line":
                    lines.append(payload)
                elif kind == "done":
                    done = payload
                elif kind == "error":
                    error = payload
        except queue.Empty:
            pass

        if lines:
            self._append_log("\n".join(lines))
        if error:
            self._append_log(error)
            if done is None:
                self._finish(None)
        if done is not None:
            self._finish(done)

        try:
            self.root.after(POLL_MS, self._poll_queue)
        except tk.TclError:
            pass

    def _finish(self, code):
        if not self.running:
            return
        self.running = False
        self.proc = None
        try:
            self.start_btn.configure(state="normal")
        except tk.TclError:
            return

        stamp = time.strftime("%H:%M:%S")
        if code == 0:
            self._append_log("[{}] 提取完成（退出码 0）".format(stamp))
            self._set_status("提取完成")
            messagebox.showinfo(
                "完成",
                "提取完成。\n\n输出文件夹：\n{}\n\n"
                "可以点“打开输出文件夹”查看。".format(self.last_out),
            )
        elif code is None:
            self._set_status("启动失败")
            messagebox.showerror("失败", "提取未能开始，请检查日志。")
        else:
            self._append_log("[{}] 提取结束，退出码 {}（详见上方日志）".format(stamp, code))
            self._set_status("提取失败，退出码 {}".format(code))
            messagebox.showerror(
                "失败", "RePKG 返回退出码 {}。\n请查看日志中的错误信息。".format(code)
            )

    def _kill_proc(self):
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _on_close(self):
        if self.running and not messagebox.askyesno(
            "退出", "提取正在进行，确定要退出并终止 RePKG 吗？"
        ):
            return
        self._kill_proc()
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def main():
    root = tk.Tk()
    RePkgApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
