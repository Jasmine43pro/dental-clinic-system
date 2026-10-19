# 牙科诊所管理系统

作者：陈杨娟　学号：1602240104　班级：信管2401

## 课程报告

[牙科诊所管理系统分析与设计报告 PDF](report/1602240104陈杨娟-牙科诊所管理系统.pdf)

报告共100页，包含业务建模、系统需求、分析模型、数据库与实现设计，以及用例规约、序列图、状态机、数据字典和7页数据库关系图。

本项目以ADC课程案例为设计对象，岳雅口腔医院为单院区实地调研样本。运行数据中的诊所、患者、医生和金额均为虚构演示资料。

## 源码结构

| 目录或文件 | 用途 |
| --- | --- |
| app/ | Django应用、页面、静态资源、数据库迁移与自动化测试 |
| scripts/ | 数据库及静态资源准备、Linux服务启动、私有数据库备份 |
| requirements.txt | Python运行依赖 |
| .env.example | 环境变量配置项目，不含实际凭据；程序不自动加载此文件 |
| 提交文件清单.json | 当前文件的字节数与SHA256，清单本身不递归列入 |

## 环境与运行

使用Python 3.12和PostgreSQL，数据库需支持btree_gist扩展。Windows本机的项目、虚拟环境、缓存及数据库保存在D盘。

1. 建立独立Python环境，安装依赖：

   ~~~text
   python -m pip install -r requirements.txt
   ~~~

2. 在进程环境中设置DJANGO_SETTINGS_MODULE=config.settings_cloud、DATABASE_URL及独立随机DJANGO_SECRET_KEY。其他配置项目见.env.example。密钥至少43字符，实际连接和密码单独保管。
3. 本机访问时设置DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost,testserver及DJANGO_HTTPS=0；DB_SSLMODE按本机PostgreSQL实际配置设置。托管环境使用实际域名、HTTPS和可信代理设置，域名不使用星号。
4. 执行迁移与静态资源准备：

   ~~~text
   python scripts/cloud_prepare.py
   ~~~

5. Windows本机启动：

   ~~~text
   python app/manage.py runserver 127.0.0.1:8000
   ~~~

   Linux托管环境启动：

   ~~~text
   python scripts/cloud_start.py
   ~~~

首次在空库创建虚构演示资料时，设置INITIALIZE_DEMO=1和至少12字符的DEMO_INITIAL_PASSWORD；初始化后设置INITIALIZE_DEMO=0。命令不会覆盖已有演示库或重置密码。演示角色包含患者、前台、医生、诊所管理者、总部、财务和系统管理员，账号及访问范围由初始化命令建立。

访问/login/登录，/health/检查服务状态。完整业务演示须由诊所管理者准备当天尚未开始的连续排班；未来日期预约只能先验证预约与核对，签到须在预约当天办理。60分钟演示治疗需至少两个连续30分钟单元及相应周转资源。

数据库结构由app/domain/migrations/建立。备份工具为scripts/backup_database.py，私有配置及备份保存在.local目录，不纳入公开材料。

## 验证与实现范围

验证命令：

~~~text
python app/manage.py check
python app/manage.py makemigrations --check --dry-run
python app/manage.py test domain --noinput
~~~

测试使用独立测试数据库。2026年10月15日81项全量回归通过，其中包含8项六模块核心流程和10项操作状态保护测试；系统检查及迁移一致性检查通过。10月16日本机完成预约、签到、诊疗、结算、模拟收款、成本及季度报表的完整流程核对。上述日期对应既有验证结果。

系统支持连续预约与资源容量校验、本人核验、岗位及数据范围授权、受控更正、历史价格快照、模拟全额收款与经营统计。辅助人员采用诊所容量池；未接入真实支付、退款、外部团购核销、医学影像或具体护士跨店排班。性能目标尚需专项测试。英文辅助覆盖核心界面，临床自由文本保留原文。

本机系统已验证，当前尚未提供通过验收的公网业务网址。GitHub地址用于报告及源码查阅。

## 界面素材来源

诊所空间照片来自[Pavel Danilyuk在Pexels发布的照片6812453](https://www.pexels.com/photo/a-dental-equipment-in-the-clinic-6812453/)，许可见[Pexels License](https://www.pexels.com/license/)。照片用于界面装饰，不是ADC或岳雅口腔医院实拍。图标为SVG线稿，字体使用本机系统字体及后备字体。
