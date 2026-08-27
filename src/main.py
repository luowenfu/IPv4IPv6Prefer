"""IPv4 / IPv6 preference manager — professional minimal GUI."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parent
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

import tkinter as tk
from tkinter import messagebox, ttk

from connectivity import (
    AUTO_LABEL,
    CUSTOM_LABEL,
    PROBE_CHOICES,
    VERIFY_TIMEOUT_S,
    ConnectTestResult,
    hosts_for_probe_choice,
    make_timeout_result,
    run_connect_test,
)
from elevate import ensure_admin_or_relaunch, is_admin
from policy import Preference, PolicyError, get_preference, set_ipv4_prefer, set_ipv6_prefer


BG = "#FAFAFA"
SURFACE = "#FFFFFF"
FG = "#111827"
FG_SECONDARY = "#6B7280"
FG_TERTIARY = "#9CA3AF"
BORDER = "#E5E7EB"
V4 = "#047857"
V6 = "#1D4ED8"
NEUTRAL = "#6B7280"
BTN = "#111827"
BTN_TEXT = "#FFFFFF"
BTN_MUTED = "#F3F4F6"
BTN_MUTED_TEXT = "#374151"
WARN = "#B45309"
DANGER = "#B91C1C"

FONT_UI = ("Microsoft YaHei UI", 9)
FONT_UI_MED = ("Microsoft YaHei UI", 10)
FONT_TITLE = ("Microsoft YaHei UI", 22, "bold")
FONT_SECTION = ("Microsoft YaHei UI", 9)
FONT_RESULT = ("Microsoft YaHei UI", 14, "bold")
FONT_MONO = ("Consolas", 9)

BTN_HEIGHT = 40


def _color(p: Preference) -> str:
    return {Preference.IPV4: V4, Preference.IPV6: V6}.get(p, NEUTRAL)


def _label(p: Preference) -> str:
    return {
        Preference.IPV4: "IPv4 优先",
        Preference.IPV6: "IPv6 优先",
        Preference.UNKNOWN: "未能识别",
    }[p]


def _resource_path(*parts: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / Path(*parts)
    return Path(__file__).resolve().parent.parent / Path(*parts)


class Button(tk.Frame):
    """Fixed-height flat button (avoids Label vertical squeeze)."""

    def __init__(self, master, text, command, *, primary=True, **kwargs):
        self._primary = primary
        self._command = command
        self._enabled = True
        bg = BTN if primary else BTN_MUTED
        super().__init__(master, bg=bg, height=BTN_HEIGHT, **kwargs)
        self.pack_propagate(False)
        self._label = tk.Label(
            self,
            text=text,
            bg=bg,
            fg=BTN_TEXT if primary else BTN_MUTED_TEXT,
            font=FONT_UI_MED,
            cursor="hand2",
        )
        self._label.pack(expand=True, fill=tk.BOTH)
        for w in (self, self._label):
            w.bind("<Button-1>", lambda _e: self._click())
            w.bind("<Enter>", lambda _e: self._hover(True))
            w.bind("<Leave>", lambda _e: self._hover(False))

    def _click(self) -> None:
        if self._enabled and self._command:
            self._command()

    def _hover(self, on: bool) -> None:
        if not self._enabled:
            return
        if self._primary:
            bg = "#1F2937" if on else BTN
        else:
            bg = "#E5E7EB" if on else BTN_MUTED
        self.configure(bg=bg)
        self._label.configure(bg=bg)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if enabled:
            bg = BTN if self._primary else BTN_MUTED
            fg = BTN_TEXT if self._primary else BTN_MUTED_TEXT
            cursor = "hand2"
        else:
            bg, fg, cursor = BORDER, FG_TERTIARY, "arrow"
        self.configure(bg=bg)
        self._label.configure(bg=bg, fg=fg, cursor=cursor)


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("IPv4 / IPv6 优先级管理")
        self.geometry("520x600")
        self.minsize(520, 560)
        self.configure(bg=BG)
        self.resizable(False, False)
        self._busy = False
        self._policy = Preference.UNKNOWN
        self._verify_seq = 0
        self._verify_watchdog = None
        self._set_icon()

        self._build()
        self.after(50, self._on_startup)
        self.after(80, self._fit_window)

    def _set_icon(self) -> None:
        icon = _resource_path("assets", "app.ico")
        if icon.is_file():
            try:
                self.iconbitmap(default=str(icon))
            except tk.TclError:
                pass

    def _build(self) -> None:
        self._body = tk.Frame(self, bg=BG)
        self._body.pack(fill=tk.BOTH, expand=True, padx=32, pady=28)

        # Pin footer first so content growth cannot squeeze buttons.
        footer = tk.Frame(self._body, bg=BG)
        footer.pack(side=tk.BOTTOM, fill=tk.X)

        actions = tk.Frame(footer, bg=BG)
        actions.pack(fill=tk.X)

        self.btn_v4 = Button(actions, "设为 IPv4 优先", self.on_prefer_ipv4)
        self.btn_v4.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        self.btn_v6 = Button(actions, "设为 IPv6 优先", self.on_prefer_ipv6)
        self.btn_v6.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))

        self.status_var = tk.StringVar(value="就绪")
        tk.Label(
            footer,
            textvariable=self.status_var,
            bg=BG,
            fg=FG_TERTIARY,
            font=FONT_UI,
            anchor="w",
        ).pack(fill=tk.X, pady=(12, 0))

        # —— Content above footer ——
        content = tk.Frame(self._body, bg=BG)
        content.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        header = tk.Frame(content, bg=BG)
        header.pack(fill=tk.X)

        tk.Label(
            header,
            text="IPv4 / IPv6 优先级管理",
            bg=BG,
            fg=FG,
            font=("Microsoft YaHei UI", 12, "bold"),
            anchor="w",
        ).pack(side=tk.LEFT)

        self.admin_var = tk.StringVar(value="已提权" if is_admin() else "未提权")
        tk.Label(
            header,
            textvariable=self.admin_var,
            bg=BG,
            fg=FG_TERTIARY,
            font=FONT_UI,
            anchor="e",
        ).pack(side=tk.RIGHT)

        tk.Label(
            content,
            text="调整 Windows 前缀策略，决定双栈站点默认走 IPv4 还是 IPv6。不禁用 IPv6。",
            bg=BG,
            fg=FG_SECONDARY,
            font=FONT_UI,
            anchor="w",
            wraplength=450,
            justify=tk.LEFT,
        ).pack(fill=tk.X, pady=(6, 18))

        policy = self._panel(content)
        policy.pack(fill=tk.X)

        policy_head = tk.Frame(policy, bg=SURFACE)
        policy_head.pack(fill=tk.X)

        tk.Label(
            policy_head,
            text="目前策略：",
            bg=SURFACE,
            fg=FG_SECONDARY,
            font=FONT_SECTION,
            anchor="w",
        ).pack(side=tk.LEFT)

        self.btn_refresh = tk.Label(
            policy_head,
            text="重新读取",
            bg=SURFACE,
            fg=V6,
            font=FONT_UI,
            cursor="hand2",
            anchor="e",
        )
        self.btn_refresh.pack(side=tk.RIGHT)
        self.btn_refresh.bind("<Button-1>", lambda _e: self._on_refresh_click())
        self.btn_refresh.bind(
            "<Enter>", lambda _e: self._link_enter(self.btn_refresh)
        )
        self.btn_refresh.bind(
            "<Leave>", lambda _e: self._link_leave(self.btn_refresh)
        )
        self._refresh_enabled = True

        self.policy_var = tk.StringVar(value="读取中…")
        self.policy_label = tk.Label(
            policy,
            textvariable=self.policy_var,
            bg=SURFACE,
            fg=FG,
            font=FONT_TITLE,
            anchor="w",
            pady=2,
        )
        self.policy_label.pack(fill=tk.X)

        self.policy_meta_var = tk.StringVar(value="")
        tk.Label(
            policy,
            textvariable=self.policy_meta_var,
            bg=SURFACE,
            fg=FG_TERTIARY,
            font=FONT_MONO,
            anchor="w",
        ).pack(fill=tk.X, pady=(2, 0))

        verify = self._panel(content)
        verify.pack(fill=tk.X, pady=(12, 0))

        # 访问验证： 下拉框  验证  — one row
        self.verify_head = tk.Frame(verify, bg=SURFACE)
        self.verify_head.pack(fill=tk.X)

        tk.Label(
            self.verify_head,
            text="访问验证：",
            bg=SURFACE,
            fg=FG_SECONDARY,
            font=FONT_SECTION,
            anchor="w",
        ).pack(side=tk.LEFT)

        self.btn_verify = tk.Label(
            self.verify_head,
            text="验证",
            bg=SURFACE,
            fg=V6,
            font=FONT_UI,
            cursor="hand2",
            anchor="e",
        )
        self.btn_verify.pack(side=tk.RIGHT, padx=(10, 0))
        self.btn_verify.bind("<Button-1>", lambda _e: self._on_verify_click())
        self.btn_verify.bind(
            "<Enter>", lambda _e: self._link_enter(self.btn_verify)
        )
        self.btn_verify.bind(
            "<Leave>", lambda _e: self._link_leave(self.btn_verify)
        )
        self._verify_enabled = True

        self.probe_var = tk.StringVar(value=AUTO_LABEL)
        self.probe_combo = ttk.Combobox(
            self.verify_head,
            textvariable=self.probe_var,
            values=[label for label, _ in PROBE_CHOICES],
            state="readonly",
            font=FONT_UI,
            width=22,
        )
        self.probe_combo.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(8, 0))
        self.probe_combo.bind("<<ComboboxSelected>>", self._on_probe_selected)

        # Custom host entry (shown only when 自定义 is selected)
        self.custom_row = tk.Frame(verify, bg=SURFACE)
        tk.Label(
            self.custom_row,
            text="域名",
            bg=SURFACE,
            fg=FG_SECONDARY,
            font=FONT_UI,
            anchor="w",
            width=6,
        ).pack(side=tk.LEFT)
        self.custom_host_var = tk.StringVar(value="")
        self.custom_entry = tk.Entry(
            self.custom_row,
            textvariable=self.custom_host_var,
            font=FONT_MONO,
            bg="#F9FAFB",
            fg=FG,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=V6,
        )
        self.custom_entry.pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0), ipady=4
        )

        self.verify_var = tk.StringVar(value="待验证")
        self.verify_label = tk.Label(
            verify,
            textvariable=self.verify_var,
            bg=SURFACE,
            fg=FG_TERTIARY,
            font=FONT_RESULT,
            anchor="w",
            justify=tk.LEFT,
            wraplength=430,
            pady=2,
        )
        self.verify_label.pack(fill=tk.X, pady=(10, 0))

        # Vertically aligned result rows
        self.result_grid = tk.Frame(verify, bg=SURFACE)
        self.result_grid.pack(fill=tk.X, pady=(6, 0))
        self.result_vars = {
            "host": tk.StringVar(value="—"),
            "peer": tk.StringVar(value="—"),
            "v4": tk.StringVar(value="—"),
            "v6": tk.StringVar(value="—"),
        }
        result_labels = (
            ("host", "探测站"),
            ("peer", "实际返回"),
            ("v4", "IPv4"),
            ("v6", "IPv6"),
        )
        for row, (key, title) in enumerate(result_labels):
            tk.Label(
                self.result_grid,
                text=title,
                bg=SURFACE,
                fg=FG_SECONDARY,
                font=FONT_MONO,
                anchor="w",
                width=8,
            ).grid(row=row, column=0, sticky="w", pady=1)
            tk.Label(
                self.result_grid,
                textvariable=self.result_vars[key],
                bg=SURFACE,
                fg=FG_TERTIARY,
                font=FONT_MONO,
                anchor="w",
            ).grid(row=row, column=1, sticky="w", padx=(12, 0), pady=1)

        self.hint_var = tk.StringVar(value="")
        self.hint_title = tk.Label(
            verify,
            text="可能原因",
            bg=SURFACE,
            fg=FG_SECONDARY,
            font=FONT_MONO,
            anchor="w",
        )
        self.hint_label = tk.Label(
            verify,
            textvariable=self.hint_var,
            bg=SURFACE,
            fg=DANGER,
            font=FONT_UI,
            anchor="nw",
            justify=tk.LEFT,
            wraplength=360,
        )
        self._verify_card = verify
        verify.bind("<Configure>", self._on_verify_card_configure)

    def _panel(self, parent) -> tk.Frame:
        outer = tk.Frame(parent, bg=BORDER, padx=1, pady=1)
        inner = tk.Frame(outer, bg=SURFACE, padx=18, pady=14)
        inner.pack(fill=tk.BOTH, expand=True)
        inner._outer = outer  # type: ignore[attr-defined]

        def pack_override(**kwargs):
            return outer.pack(**kwargs)

        inner.pack = pack_override  # type: ignore[method-assign]
        return inner

    def _buttons(self):
        return self.btn_v4, self.btn_v6

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for btn in self._buttons():
            btn.set_enabled(not busy)
        self._refresh_enabled = not busy
        self._verify_enabled = not busy
        for link in (self.btn_refresh, self.btn_verify):
            if busy:
                link.configure(fg=FG_TERTIARY, cursor="arrow")
            else:
                link.configure(fg=V6, cursor="hand2")
        try:
            self.probe_combo.configure(state="disabled" if busy else "readonly")
            self.custom_entry.configure(state=tk.DISABLED if busy else tk.NORMAL)
        except tk.TclError:
            pass

    def _fit_window(self) -> None:
        """Grow/shrink window height so verify card (incl. 验证) stays visible."""
        self.update_idletasks()
        req_h = self._body.winfo_reqheight() + 56  # outer pady + title bar slack
        req_h = max(560, req_h)
        # Cap extremely tall layouts but allow custom-row expansion.
        req_h = min(req_h, 900)
        self.geometry(f"520x{req_h}")
        self.minsize(520, min(560, req_h))

    def _on_probe_selected(self, _event=None) -> None:
        if self.probe_var.get() == CUSTOM_LABEL:
            self.custom_row.pack(
                fill=tk.X, pady=(8, 0), after=self.verify_head
            )
            self.custom_entry.focus_set()
        else:
            self.custom_row.pack_forget()
        self.after_idle(self._fit_window)

    def _selected_hosts(self) -> tuple[str, ...]:
        return hosts_for_probe_choice(
            self.probe_var.get(),
            custom_host=self.custom_host_var.get(),
        )

    def _on_verify_card_configure(self, event) -> None:
        if event.widget is not getattr(self, "_verify_card", None):
            return
        self._sync_hint_wrap(event.width)

    def _sync_hint_wrap(self, width: int | None = None) -> None:
        if width is None:
            width = self._verify_card.winfo_width()
        wrap = max(180, int(width) - 8)
        if getattr(self, "_hint_wrap", None) != wrap:
            self._hint_wrap = wrap
            self.hint_label.configure(wraplength=wrap)
            self.verify_label.configure(wraplength=wrap)

    def _set_hint(self, text: str, *, warn: bool = False) -> None:
        text = (text or "").strip()
        self.hint_var.set(text)
        self.hint_label.configure(fg=WARN if warn else DANGER)
        if text:
            self.hint_title.pack(fill=tk.X, pady=(10, 0), after=self.result_grid)
            self.hint_label.pack(fill=tk.X, pady=(2, 0), after=self.hint_title)
            self.after_idle(self._sync_hint_wrap)
        else:
            self.hint_title.pack_forget()
            self.hint_label.pack_forget()
        self.after_idle(self._fit_window)

    def _set_result_rows(
        self,
        *,
        host: str = "—",
        peer: str = "—",
        v4: str = "—",
        v6: str = "—",
    ) -> None:
        self.result_vars["host"].set(host or "—")
        self.result_vars["peer"].set(peer or "—")
        self.result_vars["v4"].set(v4 or "—")
        self.result_vars["v6"].set(v6 or "—")

    def _link_enter(self, link: tk.Label) -> None:
        enabled = (
            self._refresh_enabled
            if link is self.btn_refresh
            else self._verify_enabled
        )
        if enabled:
            link.configure(fg="#1E40AF")

    def _link_leave(self, link: tk.Label) -> None:
        enabled = (
            self._refresh_enabled
            if link is self.btn_refresh
            else self._verify_enabled
        )
        link.configure(fg=V6 if enabled else FG_TERTIARY)

    def _on_startup(self) -> None:
        self.refresh_status()
        self._run_verify()

    def _on_refresh_click(self) -> None:
        if self._refresh_enabled:
            self.refresh_status()

    def _on_verify_click(self) -> None:
        if not self._verify_enabled or self._busy:
            return
        self._run_verify()

    def _run_verify(self) -> None:
        hosts = self._selected_hosts()
        if self.probe_var.get() == CUSTOM_LABEL and not hosts:
            messagebox.showwarning("提示", "请输入自定义探测站域名。")
            return

        self._set_busy(True)
        target = hosts[0] if len(hosts) == 1 else "自动探测站"
        self.status_var.set("正在进行访问验证…")
        self.verify_var.set("验证中…")
        self.verify_label.configure(fg=FG_TERTIARY)
        self._set_result_rows(host=target, peer="…", v4="…", v6="…")
        self._set_hint("")
        expected = self._policy
        self._start_verify_worker(hosts, expected)

    def _cancel_verify_watchdog(self) -> None:
        if self._verify_watchdog is not None:
            try:
                self.after_cancel(self._verify_watchdog)
            except tk.TclError:
                pass
            self._verify_watchdog = None

    def _start_verify_worker(
        self,
        hosts: tuple[str, ...],
        expected: Preference,
    ) -> None:
        self._verify_seq += 1
        seq = self._verify_seq
        self._cancel_verify_watchdog()
        self._verify_watchdog = self.after(
            VERIFY_TIMEOUT_S * 1000,
            lambda: self._on_verify_timeout(seq),
        )

        def worker() -> None:
            try:
                result = run_connect_test(
                    hosts=hosts,
                    expected=expected,
                    timeout_s=VERIFY_TIMEOUT_S,
                )
            except Exception as exc:
                result = ConnectTestResult(
                    ok=False,
                    host="",
                    family=None,
                    peer_ip="",
                    ipv4_ip="",
                    ipv6_ip="",
                    message="探测异常",
                    preference=Preference.UNKNOWN,
                    hint=f"验证过程出错：{exc}",
                    kind="error",
                    title="验证失败",
                )
            self.after(0, lambda r=result, s=seq: self._on_verified(r, s))

        threading.Thread(target=worker, daemon=True).start()

    def _on_verify_timeout(self, seq: int) -> None:
        self._verify_watchdog = None
        if seq != self._verify_seq or not self._busy:
            return
        # Invalidate the hung worker so a late result cannot overwrite the UI.
        self._verify_seq += 1
        self._on_verified(make_timeout_result(), self._verify_seq)

    def refresh_status(self) -> None:
        try:
            status = get_preference()
        except PolicyError as exc:
            self._policy = Preference.UNKNOWN
            self.policy_var.set("未能识别")
            self.policy_label.configure(fg=NEUTRAL)
            self.policy_meta_var.set("")
            self.status_var.set(f"读取失败：{exc}")
            return
        except Exception as exc:
            self._policy = Preference.UNKNOWN
            self.policy_var.set("未能识别")
            self.policy_label.configure(fg=NEUTRAL)
            self.policy_meta_var.set("")
            self.status_var.set(f"读取失败：{exc}")
            return

        self._policy = status.preference
        self.policy_var.set(_label(status.preference))
        self.policy_label.configure(fg=_color(status.preference))

        meta_parts = []
        if status.ipv4_mapped:
            meta_parts.append(
                f"::ffff:0:0/96  precedence={status.ipv4_mapped.precedence}"
            )
        if status.ipv6_global:
            meta_parts.append(f"::/0  precedence={status.ipv6_global.precedence}")
        self.policy_meta_var.set("    ".join(meta_parts))
        if status.repaired:
            self.status_var.set("已补全重启后丢失的默认策略")
        else:
            self.status_var.set("策略已读取")

    def on_prefer_ipv4(self) -> None:
        self._apply_change(set_ipv4_prefer, Preference.IPV4)

    def on_prefer_ipv6(self) -> None:
        self._apply_change(set_ipv6_prefer, Preference.IPV6)

    def _apply_change(self, action, target: Preference) -> None:
        if not is_admin():
            if messagebox.askyesno(
                "需要管理员权限",
                "修改系统前缀策略需要管理员权限。\n是否以管理员身份重新启动本程序？",
            ):
                if not ensure_admin_or_relaunch():
                    messagebox.showerror("无法提权", "未能获取管理员权限。")
            return

        self._set_busy(True)
        self.status_var.set(f"正在设置为{_label(target)}…")
        self.verify_var.set("验证中…")
        self.verify_label.configure(fg=FG_TERTIARY)
        self._set_result_rows(peer="…", v4="…", v6="…")
        self._set_hint("")
        self.update_idletasks()
        try:
            action()
            self.refresh_status()
            self.status_var.set(f"已设为{_label(target)}，正在验证…")
            hosts = self._selected_hosts()
            expected = self._policy
            self._start_verify_worker(hosts, expected)
        except PolicyError as exc:
            messagebox.showerror("设置失败", str(exc))
            self.status_var.set(f"设置失败：{exc}")
            self.verify_var.set("未完成")
            self._set_result_rows()
            self._set_hint(str(exc))
            self._set_busy(False)
        except Exception as exc:
            messagebox.showerror("设置失败", str(exc))
            self.status_var.set(f"设置失败：{exc}")
            self.verify_var.set("未完成")
            self._set_result_rows()
            self._set_hint(str(exc))
            self._set_busy(False)

    def _on_verified(self, result: ConnectTestResult, seq: int | None = None) -> None:
        if not self.winfo_exists():
            return
        if seq is not None and seq != self._verify_seq:
            return
        self._cancel_verify_watchdog()
        self._set_busy(False)
        self._set_result_rows(
            host=result.host or "—",
            peer=result.peer_ip or result.message or "—",
            v4=result.ipv4_ip,
            v6=result.ipv6_ip,
        )
        self._set_hint(result.hint, warn=result.warn and result.ok)

        if not result.ok:
            self.verify_var.set(result.title or "验证失败")
            self.verify_label.configure(fg=DANGER)
            self.status_var.set("访问验证未通过")
            return

        self.verify_var.set(result.title or result.preference_label)
        if result.warn:
            self.verify_label.configure(fg=WARN)
        else:
            self.verify_label.configure(fg=_color(result.preference))
        self.status_var.set("访问验证已完成")


def main() -> int:
    if not is_admin():
        if ensure_admin_or_relaunch():
            return 0
    App().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
