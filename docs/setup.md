# Cài đặt từ đầu

Giả định: đã cài `herdr` và `agy` trong `~/.local/bin`. Chạy Claude Code **bên trong một pane Herdr** (`HERDR_ENV=1`), nếu không nó không được phép điều khiển Herdr.

## 1. Script
Chép các script trong [scripts.md](scripts.md) vào `~/.local/bin/` rồi `chmod +x`.

## 2. Profile cô lập (mỗi tài khoản một thư mục)
```bash
mkdir -p ~/.agy_profiles/acc2 ~/.agy_profiles/acc3 ~/.agy_profiles/acc4
```
`agy-as accN` đặt `HOME`/`XDG_*` về `~/.agy_profiles/accN`, chạy D-Bus và gnome-keyring riêng → token và quota từng tài khoản tách biệt.
Không bao giờ chạy `agy` trần (sẽ dùng tài khoản thật của máy). Không commit `~/.agy_profiles`.

## 3. Đăng nhập (làm tay một lần mỗi tài khoản)
Mở một ô shell trống trong Herdr: `agy-as acc2`, rồi đăng nhập Google khi được hỏi. Phiên được lưu trong profile. Có thể chạy các tài khoản song song.

## 4. Skill dùng chung
Kho: `~/.gemini/config/skills/<tên>/SKILL.md`. Gắn vào từng profile bằng symlink:
```bash
for a in acc2 acc3 acc4; do
  d=~/.agy_profiles/$a/.gemini/config/skills; mkdir -p $d
  for s in ponytail systematic-debugging test-driven-development verification-before-completion; do
    ln -sfn ~/.gemini/config/skills/$s $d/$s
  done
done
```
Chỉ gắn skill nào worker thực sự cần: mô tả skill nằm trong ngữ cảnh mỗi lượt.

## 5. Luật cho worker
```bash
mkdir -p ~/.gemini/worker-rules && cp worker-rules/GEMINI.md ~/.gemini/worker-rules/
for a in acc2 acc3 acc4; do ln -sfn ~/.gemini/worker-rules/GEMINI.md ~/.agy_profiles/$a/.gemini/GEMINI.md; done
```
agy nạp `GEMINI.md` làm luật toàn cục (giới hạn 24 KB/file). Sửa một chỗ, áp cho mọi worker; khởi động lại worker để nạp.

## 6. Git cho worker
Danh tính để truy vết commit; worker chỉ commit cục bộ, người/supervisor push.
```bash
for a in acc2 acc3 acc4; do
  git config --file ~/.agy_profiles/$a/.gitconfig user.name  "agy-$a"
  git config --file ~/.agy_profiles/$a/.gitconfig user.email "agy-$a@local"
done
```
Worker không dùng được SSH key hay đăng nhập `gh` của máy chính.

## 7. Role và khởi động
Chép `roles.conf` (trong [scripts.md](scripts.md)) vào `~/.agy_profiles/roles.conf`. Trong mỗi ô shell trống chạy `agy-as accN`, rồi từ pane supervisor:
```bash
agy-sync        # đặt tên <acc>-<role> và mode mặc định cho mọi agy đang chạy
herdr agent list
```
Bước kiểm tra nhanh: hỏi mỗi worker (read-only) nó thấy skill nào và có luật `GEMINI.md` không.
