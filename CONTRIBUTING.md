# 貢獻流程 (Contribution Workflow)

本專案的協作方式如下：

## 角色
- **Owner**：倉庫建立者，擁有最高權限。
- **Collaborator**：被授權的協作者，擁有寫入權限。
- **Contributor**：沒有寫入權限的人，透過 fork + PR 方式貢獻。

---

## Owner / Collaborator 的開發流程
即使擁有寫入權限，仍建議透過 **branch + PR** 進行開發，以保持程式碼品質。

1. **建立分支**
   - 從 `main` 建立新分支
   - 命名建議：
     - `feature/<功能名稱>` 例：`feature/login`
     - `bugfix/<問題描述>` 例：`bugfix/crash-on-startup`

2. **開發與提交**
   - 在分支上進行修改
   - 撰寫清楚的 commit message

3. **發送 PR**
   - 從該分支開一個 Pull Request 到 `main`
   - 指定至少一位 Collaborator 作為 Reviewer（包含 Owner 本人也可以指定）

4. **Review & Merge**
   - Reviewer 進行程式碼審查（Code Review）
   - 如果需要修改，提交者在同一分支更新程式碼
   - 通過後，Reviewer 或 Owner 將 PR merge 進 `main`

---

## 外部 Contributor 的流程
1. Fork 本專案到自己的帳號
2. 建立分支並進行修改
3. Push 到自己帳號的 repo
4. 發送 Pull Request 回本專案的 `main`
5. 由 Collaborator/Owner 進行 Review & Merge

---

## 注意事項
- 請避免直接 push 到 `main`
- 一律透過 PR 進行程式碼合併
- Commit message 需清楚描述修改內容
- 建議在 PR 描述中寫明：
  - 修改動機
  - 解決的問題
  - 測試方式
