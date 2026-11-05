# MultiTranslate 多引擎翻译

Alfred 5 文本翻译 Workflow，支持火山引擎、百度、阿里云与微软 Azure。纯 Python 标准库，无 pip 运行依赖。需要 Alfred Powerpack，以及可用的 Python 3.10 或更新版本。

## 安装与开始

1. 双击 `dist/MultiTranslate.alfredworkflow`，由 Alfred 导入。
2. 运行 `trconfig`，用文本编辑器打开配置文件。
3. 填写要使用的服务商密钥，关闭不使用的引擎，确认 `default_engine` 与 `fallback_order`，保存 JSON。
4. 运行 `tr hello`；选中成功结果按回车复制完整译文。默认不会自动粘贴到其他应用。

Workflow 不自带 Python，也不会自动安装依赖。如果显示缺少解释器，先安装 Python 3.10+（可通过 Python 官网或已有包管理器）。在终端运行 `command -v python3` 查看位置，再到 Workflow 的环境变量设置中将 `PYTHON_BINARY` 填为你自己的解释器位置；默认留空从 Alfred 的 PATH 查找。不要把自己的路径提交或打包。

代码不会自行调用真实 API 做自检。首次使用前请确认账号已开通对应服务及计费方式。

## 输入

| 输入 | 行为 |
| --- | --- |
| `tr hello` | 自动识别源语言，默认引擎译为简体中文 |
| `tr v:hello` | 火山引擎 |
| `tr b:hello` | 百度 |
| `tr a:hello` | 阿里云 |
| `tr m:hello` | Azure |
| `tr all:hello` | 并发调用全部启用的引擎，每家一行 |
| `tr set:en 你好` | 默认引擎译为英文；只对本次输入生效 |
| `tr v:set:ja 你好` | 火山引擎译为日文 |
| `tr -- v:hello` | 将 `v:hello` 当作原文 |
| `tr v:-- set:en hello` | 火山将 `set:en hello` 当作原文 |
| `trconfig` | 打开实际用户配置 |

目标语言：`zh` 简体中文、`en` 英语、`ja` 日语、`ko` 韩语、`fr` 法语、`de` 德语、`ru` 俄语。未提供繁体语言选项。`set:` 后使用一个空格分隔原文；剩余空白、换行保留。空输入只给提示。

前缀只在开头按上述顺序识别；URL、正文中的冒号不会被拆开。只有无引擎前缀时才兜底，显式指定和 `all:` 不会悄悄换引擎。关闭默认引擎时，无前缀调用在允许兜底的情况下会跳过它。

`all:` 的显示顺序沿用 `fallback_order`，其他启用引擎追加在后面；缺配置的引擎会显示独立错误。普通调用最多请求三个不同的服务商，跳过禁用/缺配置项；认证、额度、限流、网络、暂时服务故障可切换，输入问题不重试。成功结果会标明实际引擎与兜底来源。

输入延迟使用 Alfred 的自动延迟，首字符不立即请求，旧查询可终止。已经发送到云端的请求不能保证撤销计费；`all:` 与自动兜底可能消耗多家额度。

## 配置与数据

发行包只含 `config.example.json`，不含真实密钥。运行时首次以权限 0600 创建用户 `config.json`，之后绝不覆盖它。实际位置通过 `alfred_workflow_data` 获取；缓存和日志通过 `alfred_workflow_cache` 获取。未在 Alfred 内运行时，按当前用户主目录动态定位对应数据目录。设置了稳定 Bundle ID：`local.multitranslate.alfred`。

JSON 不能写注释，下面是字段说明；删去可选全局字段会使用模板默认值。引擎对象缺少字段时使用该引擎的默认字段，删除整个引擎对象则不再启用它。

| 字段 | 用途、默认值与约束 |
| --- | --- |
| `default_engine` | 无前缀调用的首选引擎，默认 `volc`；取已安装适配器 ID |
| `fallback_enable` | 是否为无前缀调用启用兜底，默认 `true`；布尔值 |
| `fallback_order` | 兜底及 all 的展示优先级，默认 `volc, azure, aliyun, baidu`；字符串数组，重复项自动去重 |
| `target_lang_default` | 未使用 set 时的目标语言，默认 `zh`；取上述七种语言 |
| `request_timeout_seconds` | 单家请求等待上限，默认 10 秒，范围 0.1–10 |
| `query_timeout_seconds` | 整次查询的调度等待预算，默认 15 秒，范围 0.1–60；各请求同时受剩余预算约束 |
| `cache_enabled` | 成功结果缓存开关，默认 `true` |
| `cache_ttl_seconds` | 缓存有效期，默认 300 秒，范围 1–3600 |
| `cache_max_text_chars` | 可缓存原文长度，默认 1000 字符，整数 1–50000；更长文本仍可翻译 |
| `cache_max_entries` | 缓存条目上限，默认 256，整数 1–1000；同时有 2 MiB 文件上限 |
| `log_enabled` | 诊断日志开关，默认 `false`；布尔值 |
| `engines.*.enable` | 单个引擎是否参与调用，模板均为 `true`；未填写密钥不会发请求 |
| `engines.volc.ak / sk` | 火山 AccessKey ID / Secret，默认空，启用调用时必填 |
| `engines.volc.region` | 火山 TranslateText 签名地域，默认并要求 `cn-north-1` |
| `engines.baidu.appid / appkey` | 百度翻译开放平台应用 ID / 密钥，默认空，启用调用时必填 |
| `engines.aliyun.ak / sk` | 阿里云 AccessKey ID / Secret，默认空，启用调用时必填 |
| `engines.aliyun.region` | 当前使用杭州接入点，默认并要求 `cn-hangzhou` |
| `engines.azure.key` | Azure Translator 订阅密钥，默认空，启用调用时必填 |
| `engines.azure.region` | 默认空，适用于全球资源；区域或多服务资源必须填写实际区域，如 `eastasia`，不能随意套用 |
| Workflow 环境变量 `PYTHON_BINARY` | 默认空，使用 PATH 中的 python3；需要时由用户提供解释器位置，不是 JSON 字段 |

缓存键隔离服务商、原文、源/目标语言与缓存格式版本。只保存短文本的成功结果，不保存密钥、认证头或失败；JSON 有跨进程锁和原子替换，损坏或忙时退化为未命中。缓存含原文与译文，需要时可以删除 `translations.json` 清除。日志名为 `workflow.log`，最多一个备份，每个文件 256 KiB；仅记录引擎、错误码、HTTP 状态、请求 ID、耗时和缓存状态。

超过服务商单次上限时不截断、不发送：火山和阿里 5000 字符，百度 6000 UTF-8 字节，Azure 50000 字符。`all:` 中各家仍使用同一份原文，超限项独立报错。

## 申请密钥与计费

以下官方资料核对于 2026-11-05；优惠、免费额度、套餐和地域可能调整，以你账号的实际控制台为准。本项目不保证免费，也不自动充值。

- 火山：在[火山控制台](https://console.volcengine.com/)开通机器翻译，获取有调用权限的 AccessKey。[接口](https://docs.volcengine.com/docs/MachineTranslation/TextTranslationAPI?lang=zh)、[计费](https://docs.volcengine.com/docs/MachineTranslation/Productbilling?lang=zh)。本适配器调用 TranslateText 2020-06-01，不是豆包翻译模型。
- 百度：在[百度翻译开放平台](https://fanyi-api.baidu.com/)开通通用文本翻译，取得 AppID 和密钥。[通用接口及错误码](https://fanyi-api.baidu.com/doc_bd/21)。不要使用百度智能云其他产品的 AK/SK 代替。
- 阿里云：在[阿里云控制台](https://home.console.aliyun.com/)开通机器翻译通用版，为账号授予调用权限并获取 AccessKey。[TranslateGeneral](https://help.aliyun.com/zh/machine-translation/developer-reference/api-alimt-2018-10-12-translategeneral)、[V3 签名](https://help.aliyun.com/zh/sdk/product-overview/v3-request-structure-and-signature)。不是百炼 Qwen-MT API Key。
- Azure：在[Azure Portal](https://portal.azure.com/)创建 Translator 资源，在 Keys and Endpoint 页面获取密钥及地域。[认证](https://learn.microsoft.com/en-us/azure/ai-services/translator/text-translation/reference/authentication)、[价格](https://azure.microsoft.com/en-us/pricing/details/cognitive-services/translator/)。本实现是全球公有云 Translator 3.0；不覆盖中国云、专用网络、自定义域名或 token 鉴权。

## 签名与错误排查

- 火山：UTC 时间 `X-Date`、签名地域和 service 必须一致；签名的是实际 UTF-8 请求体字节，不能签完再改 JSON。HMAC 派生顺序是日期、地域、服务、`request`，不添加其他服务商的密钥前缀。[官方签名源码](https://raw.githubusercontent.com/volcengine/volc-sdk-python/main/volcengine/auth/SignerV4.py)。官方未穷举额度错误，本实现仅对明确表示余额/额度耗尽的消息做保守识别。
- 百度：MD5 输入为 `appid + 原文 + salt + appkey`；先签原文，再表单编码。日语 `jp`、韩语 `kor`、法语 `fra` 由适配器转换。`54001` 是签名错误，`52003` 是未授权。多段结果全部连接，不只取第一段。
- 阿里云：使用 `ACS3-HMAC-SHA256`；表单请求体的编码、SHA256 摘要、规范头和实际发送字节必须一致；`content-type` 也参加签名。`10013` 同时可能表示未开通或欠费，不能武断显示为免费额度用尽。
- Azure：简体中文用 `zh-Hans`，自动源语言省略 `from`；全球与区域资源的 header 要匹配。`403001` 映射额度问题，不能将所有 403 当密钥错误。

所有 API 统一返回 success、engine、original_text、translated_text、from_lang、to_lang、raw_error、error_code；另含 vendor_code、request_id、http_status、cached、fallback_from、elapsed_ms。未知源语言用 `und`。`raw_error` 是安全的中文说明，不保留可能回显正文/密钥的原始服务商消息；具体码和请求 ID 用于追查。失败不修改剪贴板。

## 项目文件

```text
main.py                 入口、配置引导、日志初始化
config.py               配置读取和用户目录
config.example.json     无密钥模板
models.py / parser.py   统一数据结构与前缀解析
scheduler.py            默认、指定、并发及兜底调用
cache.py                五分钟 JSON 缓存
http_client.py          urllib 传输
output.py / utils.py    Alfred 输出与小型编码函数
adapters/
  __init__.py / base.py  自动发现与统一适配器协议
  volc.py / baidu.py / aliyun.py / azure.py
bootstrap.sh            解释器检查与 argv 透传
info.plist              已连好的 Alfred 对象
build_workflow.py       白名单打包
tests/                  离线功能验证
VALIDATION.md           实际验证记录及未验证范围
```

## 手工搭建与开发

已有 `info.plist` 可以直接导入安装包。手工搭建时：

1. 新建空 Workflow，Bundle ID 设为 `local.multitranslate.alfred`，将运行文件放入 Workflow 目录。
2. 添加 Script Filter：关键词 `tr`、Argument Optional、关闭 Alfred filters results、语言选 bash、输入方式选 argv；脚本为 `exec bash ./bootstrap.sh translate "$@"`。
3. Run Behaviour 选择终止前一个脚本、输入停止后自动延迟，关闭首字符立即执行；保留原始空白。
4. 连接 Copy to Clipboard：使用传入文本（文本框留空），关闭自动粘贴、开启忽略动态占位符。这样原文中的 `{date}` 等不会被再次展开。
5. 添加无参数 Keyword `trconfig`，连接 Run Script，脚本为 `exec bash ./bootstrap.sh configure`。

在原项目根目录执行离线功能验证与打包（不访问云端、不安装 Workflow）：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -v
python3 -B build_workflow.py
```

测试临时数据只在项目内创建并清理；测试失败输出应先脱敏再落盘。lint 和其他独立检查需另行授权。安装包白名单排除测试、计划、真实配置、日志、缓存和字节码；不生成中间项目副本。

## 增加或删除服务商

在 `adapters/` 新增一个 Python 文件，导出继承 `BaseAdapter` 的 `Adapter` 类，声明唯一的 `engine/name/prefix`、`required_fields`、`language_map`、长度约束，并实现 `build_request` 和 `parse_response`。`translate`、网络异常保护和通用输出由现有协议复用，不要在适配器打印 JSON。

在用户配置 `engines` 添加对应密钥与 `enable`，需要时调整 `fallback_order/default_engine`，然后重新打包；无需修改 main 或 scheduler。移除服务商时删除适配器及相应配置项，避免将它留作默认引擎。新增适配器必须像现有实现一样保留 TLS 校验，不能引入跨域重定向或记录认证信息。

## 验证范围与移除

离线成功不等于账号接入成功。真实 API、计费、Alfred 导入与回车复制需要在你的实际环境完成验证；具体已执行项目见项目根目录 `VALIDATION.md`。不应根据代码或截图声称已通过真实接入。

移除时在 Alfred 删除本 Workflow 即停止使用。用户数据不会自动删除，以免丢失密钥；可由用户自行清理本 Bundle ID 的数据与缓存。
