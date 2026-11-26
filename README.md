# 牙科诊所管理系统

陈杨娟｜信管2401班｜学号 1602240104

本项目使用 Django 和 PostgreSQL，支持预约、排班、签到接诊、诊疗记录、模拟结算、耗材成本和季度统计。系统使用虚构数据。

## 作业材料

请将[分析与设计报告](report/1602240104陈杨娟-牙科诊所管理系统.pdf)与[实现范围与验收补充说明](report/实现范围与验收补充说明.md)一并查阅。两份文件共同构成本次提交的分析设计材料。当前源码实现 40 个领域模型。报告数据字典列出编制时的 38 个已实现模型，剩余两项由补充说明列明。

- [患者端微信小程序源码](小程序主线/患者端)

## 从源码运行

以下步骤适用于 Windows。请预装 Python 3.12 和 PostgreSQL 18，并将仓库下载到 D 盘。下列数据库工具路径可按实际安装位置调整。

```bat
set "DENTAL_PG_BIN=D:\PostgreSQL\18\bin"
py -3.12 -B scripts\bootstrap.py --install
启动系统.cmd
```

首次启动会创建本项目专用数据库、应用迁移并生成虚构演示数据。打开 [本机登录页](http://127.0.0.1:8000/login/)，从本机的 `.local/demo-accounts.json` 读取各岗位演示账号和随机密码。用 `demo_reception_a` 登录，在「预约记录」打开 `DEMO-A005`，依次点击「查看对应就诊」「查看结算单」，可核对 100.00 元、已支付和模拟现金。

默认数据库端口为 55433，网页端口为 8000。首次启动前可通过 `DENTAL_DB_PORT` 和 `DENTAL_WEB_PORT` 指定空闲端口。按 Ctrl+C 停止网页服务。停止本项目数据库可运行 `.venv\Scripts\python.exe -B scripts\database.py stop`。

首次初始化会生成「云杉路店」等虚构门店及人员样例。如果已运行过旧版，可在本机数据库启动后执行 `.venv\Scripts\python.exe -B app\manage.py rename_demo_labels`，更新匹配旧占位值的样例资料。原有业务编号、病历、结算明细及记录间的关联保留。

微信开发者工具可打开「小程序主线/患者端」做本地联调，默认连接本机 8000 端口。真机体验需要获授权的体验码及可访问的 HTTPS 服务。

## 教师网页验收

[教师只读验收入口](https://203.195.214.159/login/?next=/appointments/b698d992-1074-428d-bbd4-f5c57c682795/)使用 2026 年 11 月 21 日的业务功能，已于 11 月 26 日更新界面文案和样例资料。在[在线验收操作说明](report/在线验收操作说明.md)中查看网页和微信小程序的验收账号与步骤。网页可核对 50.00 元的模拟现金已支付记录。仓库中的其他功能改进可按本机启动步骤体验。

## 回归测试

启动本机数据库后，可在独立测试库运行：

```bat
.venv\Scripts\python.exe -B app\manage.py check
.venv\Scripts\python.exe -B app\manage.py test domain --keepdb --noinput
```
