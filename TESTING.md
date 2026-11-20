# 测试记录与验证边界

本目录记录配对研究功能实际跑通的验证，不写未实现的承诺。

## 运行方式

```bash
cd backend
python3 -m pip install --user --break-system-packages \
  numpy==2.2.1 scipy==1.15.1 fastapi==0.115.6 sqlalchemy==2.0.36 \
  pydantic==2.10.4 pytest==8.3.4 httpx==0.28.1 PyJWT==2.10.1
python3 -m pytest tests/ -q
```

测试用 SQLite 与内存队列替身；不需要 Redis/Postgres 即可运行。生产仍用
docker compose 的 Postgres + Redis + 独立 pairing-worker（见 `docker-compose.yml`）。

## 覆盖内容

| 文件 | 验证点 |
|---|---|
| `tests/test_pairing_algorithm.py` | 120 组随机小例与独立穷举 oracle 逐例一致；单配对、无配对原因、偶数取中间两项平均、同成本不同中位数（`0.25` vs `1.15`）、40×40 满载确定性重跑、非法输入拒绝 |
| `tests/test_pairing_race.py` | 真实 SQLAlchemy 会话 + 线程：更正两册原子提交；冻结快照在并发更正下不撕裂；强制在两册读取之间插入更正，验证 revision 检查生效并重试；冻结后更正不改写历史结果；复核不可覆盖 |
| `tests/test_pairing_api.py` | TestClient：角色权限（观察员不可写/不可发起）、发起研究→worker 计算→复核闭环、无配对返回原因、非法允许时差在入队前拒绝、同次更正重复编号拒绝 |
| `tests/test_pairing_worker.py` | worker 结果 JSON 结构与页面消费一致；按明细复算两倍中位数与后端一致；重复消费幂等；篡改快照导致 failed |

最近一次运行：`139 passed`（算法 126 + race 5 + api 5 + worker 3）。

## 已证伪的失败模式（修复并留了回归测试）

1. **HiGHS 数值不稳**：带选择器 + 同成本约束的 MILP 在 presolve 开/关下都曾
   返回非最优解（小例可复现）。因此中位数界改为纯 Hungarian 指派，不再依赖
   MILP。
2. **身份映射读到旧值**：`freeze_snapshot` 重试时若不清会话缓存，会拿到上一轮
   的读数与旧 revision，看似一致实为撕裂快照（回归测试见
   `test_torn_read_is_detected_and_retried`；移除 `expire_all()` 后该测试失败，
   已验证）。
3. **浏览器复算单位错误**：页面用整数 micro 复算两倍中位数，早期实现的小数位
   拼接会导致一致结果被误判为不一致（已修正并用 Node 冒烟脚本验证）。

## 未验证 / 已知边界

- 未在真实 Postgres 上跑并发压力；行锁与 `expire_all` 语义按 SQLAlchemy 文档
  实现，SQLite 测试覆盖逻辑但不覆盖真实锁竞争。
- 未跑完整 docker compose（当前环境无 Docker）；容器编排、healthcheck 与
  Redis 队列仅做了静态检查。
- 规模上限：每册 40 份、允许时差 1–1440 分钟；40×40 满载分析约 0.7 秒。
- 结果是确定性的业务核算，不是统计推断；上下界只覆盖“同成本配法”这一集合。
