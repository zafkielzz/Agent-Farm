# Agent Farm

Cách dùng **Herdr** để một agent giám sát (Claude Code) điều phối nhiều worker **Antigravity CLI (`agy`)**, mỗi worker một tài khoản Google cô lập.
Repo này chỉ chứa tài liệu và skill, không có ứng dụng. Phiên bản web hub cũ đã bỏ (xem lịch sử git nếu cần).

## Bức tranh chung

```
Claude Code (supervisor, trong pane Herdr)
   │  herdr agent prompt / read / wait      agy-ask (việc một lần, không mở pane)
   ▼
acc2-impl      acc3-qa        acc4-review     ...   mỗi worker = 1 profile ~/.agy_profiles/accN
(accept-edits) (accept-edits) (plan)                (HOME, D-Bus, keyring, quota riêng)
```

- Supervisor giữ việc khó; worker nhận việc nặng nhưng cơ học. Luật trả lời ngắn cho worker nằm ở `worker-rules/GEMINI.md`.
- Tên agent = `<acc>-<role>`, mode mặc định theo role; `agy-sync` đặt lại sau mỗi lần khởi động.

## Đọc theo thứ tự

| File | Nội dung |
|---|---|
| [docs/setup.md](docs/setup.md) | Cài đặt từ đầu: profile, đăng nhập, skill dùng chung, git, luật worker |
| [docs/orchestration.md](docs/orchestration.md) | Lệnh Herdr, role, đổi mode, mẫu giao việc, điều đã/chưa kiểm chứng |
| [docs/scripts.md](docs/scripts.md) | Mã nguồn `agy-as`, `agy-ask`, `agy-up`, `agy-mode`, `agy-sync`, `roles.conf` |
| [.claude/skills/agy-orchestrator/SKILL.md](.claude/skills/agy-orchestrator/SKILL.md) | Skill cho Claude Code khi điều phối |
| [worker-rules/GEMINI.md](worker-rules/GEMINI.md) | Luật tiết kiệm token cho worker agy |
| [CLAUDE.md](CLAUDE.md) | Chỉ dẫn vai trò orchestrator cho Claude Code |

Yêu cầu: Linux, `herdr` (đã thử 0.9.5), `agy`, `dbus-daemon`, `gnome-keyring-daemon`, `python3`.
