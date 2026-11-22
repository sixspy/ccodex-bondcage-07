# 保证金笼

> 遵守 `ops/底座生成前必读_效率与技术栈.md`（≥2000 行像产品）。  
> FastAPI + Vue 3 + SQLite；`docker compose up -d --build` 一键起。  
> **禁止同构换皮**：主链路不是「占位→确认入册」也不是「入轨→裁定赔付」也不是「业务窗裁切物化」。

## 骨架一句话

保证金先入笼冻结，履约核验通过才解笼拨付，违约进罚没册，笼代与拨付罚没对账。

## 基本信息

| 项 | 值 |
|----|-----|
| 产品名 | 保证金笼 |
| 目录 | `11-bondcage` |
| 接口 | http://127.0.0.1:11317 |
| 库 | `data/app.db` |
| 账号 | admin / clerk / viewer ，密码均为 123456（viewer 只读） |

## 厚度

| 项 | 内容 |
|----|------|
| 业务 LOC | **≥2900**（py+前端，不含 vue.global / Docker / README；自检约 2984） |
| 后端模块 | db / auth / locks / bonds_svc / cages_svc / attest_svc / release_svc / forfeit_svc / reconcile_svc / audit_svc / topup_svc / split_svc |
| 前端视图 | 保证金户 / 入笼冻结 / 履约核验 / 解笼拨付 / 罚没册 / 对账互证 / 审计争用 / 增补拆笼空桩（8 页） |
| 空桩 | topup（增补入笼）、split（拆笼分拨）——前后端均有 |

## 入笼超额命运（当日锁死）

采用**乙路径**：可用额不足时正式笼零建、账户零改，只落拒入登记占住当日意图
额度；同日再入须先撤销登记，否则继续拒绝并记「超额偏航」。详见
`docs/入笼超额命运说明.md`，常量在 `backend/db.py: FREEZE_OVER_LIMIT_FATE`。

新增只读/写面：`GET /api/cages/freeze-rejections`、
`POST /api/cages/freeze-rejections/{id}/revoke`、`GET /api/reconcile-party/{party_key}`。

## 主链路

开户入金 → 按合约入笼冻结（笼代）→ 履约核验通过 → 解笼拨付到收款方 / 违约罚没 → 笼代余额与拨付+罚没对账

## 互斥切口候选

- 解笼失败整笔回滚 vs 影子拨付后晋升
- 罚没立即入册 vs 影子罚没待复核晋升
- 增补同笼扩代 vs 新笼桥接双写
- 拆笼整笔事务切分 vs 影子子笼后晋升

## 启动

```powershell
cd projects/11-bondcage
docker compose up -d --build
```

浏览器：http://127.0.0.1:11317/

本机开发：

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app:app --app-dir backend --host 127.0.0.1 --port 10608
python -m pytest -q
```
