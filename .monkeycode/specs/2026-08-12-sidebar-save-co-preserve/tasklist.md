# 需求实施计划

目标：改造侧边栏 Save 的保存逻辑，在保存时保留 `.co/` 版本历史，避免 LO `storeToURL` 重写文件导致 `.co/` 目录丢失。

流程：解析路径 -> `co export` 备份历史 -> `storeToURL` 保存 -> `co import --force` 恢复历史 -> `co commit` 提交新快照。

- [ ] 1. 新增保存管线纯 Python 模块 `src/coffice/sidebar/co_save.py`
  - [ ] 1.1 实现 co 二进制定位 `find_co_binary()`：依次检查 `CO_BIN`、`COFFICE_CO_BIN`、PATH 上的 `co`、`~/.local/bin/co`、`/usr/local/bin/co`，返回路径或 None
  - [ ] 1.2 实现 `backup_history(path) -> str | None`：CLI 可用时执行 `co export <path> --output <tmp>.co-bundle`，否则用 zipfile 提取 `.co/` 条目写 bundle；文档无历史时返回 None
  - [ ] 1.3 实现 `restore_history(path, bundle)`：CLI 可用时执行 `co import <path> <bundle> --force`（SHA 校验，必须 force），否则用 zipfile 将 bundle 的 `.co/` 条目注入文档
  - [ ] 1.4 实现 `commit_snapshot(path)`：仅 CLI 可用时执行 `co commit -m <msg> <path>`（env `CO_AUTHOR_NAME=human`），返回 commit hash 或 None
  - [ ] 1.5 错误处理：co 调用失败抛出 `CommandError`，异常信息携带 bundle 路径供手动恢复提示

- [ ] 2. 改写 `_UnoDocBridge.save`（`extension/python/coffice_panel.py:217`）接入管线
  - [ ] 2.1 新增 `_url_to_path()` 辅助：把 `doc.getLocation()` 的 file:// URL 转本地路径
  - [ ] 2.2 解析目标路径：优先参数 path，否则从 `doc.getLocation()` 解析
  - [ ] 2.3 无路径（新建未保存文档）→ fallback 到 `.uno:Save`（保持现有行为）
  - [ ] 2.4 有路径 → `backup_history` -> `doc.storeToURL` -> `restore_history` -> `commit_snapshot`，返回保存路径
  - [ ] 2.5 保存失败或 restore 失败时抛 `CommandError`，在错误信息中给出 bundle 保留位置

- [ ] 3. 更新 `extension/build.sh` 同步新模块
  - [ ] 3.1 把 `src/coffice/sidebar/co_save.py` 加入复制列表（与 contract/doc_commands 相同的 header + 扁平化处理）
  - [ ] 3.2 把 `python/coffice/co_save.py` 加入 .oxt 打包文件列表
  - [ ] 3.3 更新 `tests/sidebar/test_oxt_structure.py` 校验 shipped co_save.py 与源一致

- [ ] 4. 编写单元测试 `tests/sidebar/test_co_save.py`
  - [ ] 4.1 测试 co 二进制定位顺序（env 优先、PATH、固定路径）
  - [ ] 4.2 测试 backup/restore 的 zip fallback（无 CLI 时 .co/ 条目被备份并注入）
  - [ ] 4.3 测试无历史文档跳过管线、CLI 失败路径

- [ ] 5. 检查点：`make lint` 与 `make test` 全部通过，`.oxt` 构建成功

- [ ] 6. 手动验证（可选，需 LO/co 环境）：打开含 `.co/` 的文档 -> 点击侧边栏 Save -> 确认文件保留历史且新增一条 commit
