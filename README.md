# MultiTranslate 多引擎翻译

Alfred 5 文本翻译 Workflow，支持火山引擎、百度、阿里云、微软 Azure 和有道。1.6.0 新增独立 macOS 配置窗口，厂商之间使用横贯左右的分割线，密钥默认隐藏。保留全局代理与厂商覆盖、已有 JSON 一次导入、自动中英互译及手动 `set:` 优先。

需要 Alfred Powerpack 和 Python 3.10+。翻译及配置后端只使用 Python 标准库，无 pip 依赖；独立窗口使用系统 AppKit，安装包附带 Intel / Apple Silicon 通用程序，日常使用不需要 Swift 编译器。独立窗口要求 macOS 11+；更早系统仍可使用原生配置面板或 JSON 入口。

## 安装与开始

1. 双击 `dist/MultiTranslate.alfredworkflow`，在 Alfred 中完成导入。
2. 在 **Alfred 搜索浮窗**输入 `trconfig` 并回车，自动导入已有 JSON 设置并打开独立配置窗口。全新安装、没有 JSON 时显示默认设置和空密钥。
3. 勾选 **使用窗口配置**，然后在 **默认服务商** 下拉框中选择要用的厂商。已有的配置来源开关保持原状态，不会因升级自动切换。
4. 向下找到该厂商，确认 **启用** 开关及密钥；已有 JSON 成功导入后不必重新填写。全新安装只需配置一家即可；其他厂商保持关闭。例如选择阿里云，就启用阿里云并填写它的 AccessKey ID、AccessKey Secret，地域保留 `cn-hangzhou`。
5. 根据需要设置自动兜底及服务商顺序，点击窗口右下角 **保存**。默认服务商也需要勾选启用；填写了密钥但未启用的厂商不会被调用。
6. 确保 Workflow 已启用，再用平时的快捷键打开 **Alfred 搜索浮窗**。输入 `tr hello` 自动译成中文，或输入 `tr 你好` 自动译成英文；选中成功结果按回车复制完整译文。默认不会自动粘贴到其他应用。

窗口分为全局设置、全局代理、五家厂商和运行环境，各组之间有横跨标签与输入框两列的真正分割线。每家标题右侧是启用开关，组内按 **凭据 → 地域（如适用）→ 代理模式 → 代理地址** 排列；向下滚动可配置全部厂商，底部操作始终可见。

密钥和代理地址默认遮罩，点击右上角 **显示密钥和代理地址** 可临时查看。**取消** 或关闭窗口不会保存本次表单修改；有未保存内容时会再次确认。首次打开前已经完成的 JSON 自动导入仍保留。保存失败会显示原因并保留表单内容；若其他窗口改了设置，会拒绝覆盖，需重新打开后再编辑。

窗口底部的 **原生面板…** 或命令 `trconfig-native` 可打开 Alfred 原生配置表单；**编辑 JSON…** 或命令 `trconfig-json` 打开原 JSON。Alfred 设置页的 **Configure Workflow…** 按钮仍打开原生面板，不能替换为此独立窗口。独立窗口与原生面板共享同一套设置，建议一次只在一个窗口编辑。

**`trconfig` 是 Alfred 搜索关键词，不是终端命令。** 双击工作流编辑页面里的 `trconfig` 方块只会编辑节点属性；要打开独立配置窗口，请在平时翻译用的 Alfred 搜索浮窗输入 `trconfig`，再按回车。

已有 JSON 配置用户：更新后运行一次 `trconfig`，确认自动填入的设置，再勾选「使用窗口配置」并保存即可。导入不会自动切换配置来源，也不会改写或删除原 JSON。若要继续用 JSON，保持面板开关关闭，用 `trconfig-json` 编辑即可。详细导入规则见下方。

Workflow 不自带 Python，也不会自动安装依赖。如果显示缺少解释器，先安装 Python 3.10+（可通过 Python 官网或已有包管理器）。在终端运行 `command -v python3` 查看位置，再填入配置面板底部「Python 解释器（可选）」输入框；对应变量为 `PYTHON_BINARY`，默认留空从 Alfred 的 PATH 查找。不要把自己的路径提交或打包。

代码不会自行调用真实 API 做自检。首次使用前请确认账号已开通对应服务及计费方式。

### 使用有道翻译

1. 在[有道智云控制台](https://ai.youdao.com/console/)开通文本翻译服务，创建并绑定应用，取得 **应用 ID（appKey）** 和 **应用密钥（appSecret）**。
2. 在 Alfred 输入 `trconfig` 并回车，勾选「使用窗口配置」，向下找到「有道翻译」并启用，将两项凭据填入对应字段，保存。
3. 输入 `tr y:你好` 自动译成英文，或 `tr y:hello` 自动译成中文；`tr y:set:ja 你好` 手动指定日语。也可将默认服务商选为有道，用普通 `tr` 调用。

有道默认关闭，不需要填写地域。启用后会加入 `all:` 对比；希望其他厂商失败时由有道兜底，请在「服务商顺序」中加入 `youdao`。已保存的服务商顺序不会被升级自动改写。

若继续使用 JSON 模式，在 `engines` 对象中增加以下字段，并按需把 `default_engine` 设为 `youdao` 或将它加入 `fallback_order`。将空字符串替换为自己的应用信息，不要把密钥提交或分享。

```json
"youdao": {"enable": true, "app_key": "", "app_secret": ""}
```

JSON 字段 `app_key` 对应应用 ID，不是应用密钥。已完成一次 JSON 导入的用户，请直接在面板填写新增有道字段；升级不会重置导入标记或回填此前清空的密钥。

### 配置 HTTP 代理

在 Alfred 输入 **`trconfig`**，确保已勾选「使用窗口配置」。在「全局代理模式」选择代理方式；每个厂商下面也有独立的「代理模式」和「代理地址」。设置后保存即可，不需要修改系统代理。

| 模式 | 行为 | 地址是否使用 |
| --- | --- | --- |
| 继承全局（仅厂商可选，默认） | 使用全局模式和全局地址 | 忽略该厂商自己的地址 |
| 跟随系统（全局默认） | 使用 urllib 检测到的代理环境变量或 macOS 系统代理，并遵守 `no_proxy` 绕过规则 | 忽略面板地址 |
| 直连 | 关闭该请求的 HTTP 代理 | 忽略面板地址 |
| 指定代理 | 始终通过填写的 HTTP 代理发送，覆盖系统和 `no_proxy` 设置 | 必填 |

例如，只有 Azure 需要代理时：全局选择「直连」，Azure 选择「指定代理」并填写地址，其他厂商保留「继承全局」。全部厂商走同一代理时：全局选择「指定代理」，厂商保持「继承全局」。个别厂商仍可选择「直连」或「跟随系统」。

地址格式为 **`http://主机:端口`**，例如 `http://127.0.0.1:7890`；示例端口不是自动识别结果，请使用代理客户端实际开启的 **HTTP 或混合监听端口**。省略端口时使用 80。支持可选 Basic 认证，如 `http://用户名:密码@主机:端口`，用户名和密码中的特殊字符需做 URL 编码。地址不能包含路径、查询参数或片段。

HTTP 代理可以通过 CONNECT 转发厂商的 **HTTPS** 请求，厂商接口仍使用 HTTPS 并校验证书。这里不接受 `socks5://` 或 `https://` 代理地址，不解析 PAC 脚本。直连指不使用 HTTP 代理，仍会受系统路由、VPN/TUN 影响。「跟随系统」读取的是 Alfred 进程实际环境，终端中的临时 `export` 不一定会传给 Alfred。[Python 代理行为说明](https://docs.python.org/3.10/library/urllib.request.html#urllib.request.ProxyHandler)。

代理连接失败不会偷偷改为直连；如果启用了自动兜底，下一家使用它自己的代理设置。`all:` 中各家的代理独立，不会互相修改。命中翻译缓存时不发送新请求，因此也不会访问代理。

代理地址可能包含密码，不要分享面板截图、`prefs.plist` 或私有 JSON。所有发行代理地址为空，不会打包用户代理配置。已经完成过 JSON 自动导入的用户，请直接在面板填写这些新字段；原有一次导入标记不会被重置。

JSON 模式下，添加全局 `proxy`，并在对应引擎对象中加入 `proxy_mode`、`proxy_url`。下面只展示代理相关字段，合并时保留现有密钥等配置：

```json
{
  "proxy": {"mode": "direct", "url": ""},
  "engines": {
    "azure": {"proxy_mode": "custom", "proxy_url": "http://127.0.0.1:7890"},
    "youdao": {"proxy_mode": "inherit", "proxy_url": ""}
  }
}
```

旧 JSON 缺少代理字段时，自动采用「全局跟随系统、厂商继承全局」，不会改写原文件。

## 输入

| 输入 | 行为 |
| --- | --- |
| `tr hello` | 默认引擎自动译为简体中文 |
| `tr 你好` | 默认引擎自动译为英文 |
| `tr a:你好` | 阿里云自动译为英文 |
| `tr v:hello` | 火山引擎 |
| `tr b:hello` | 百度 |
| `tr a:hello` | 阿里云 |
| `tr m:hello` | Azure |
| `tr y:hello` | 有道自动译为中文 |
| `tr y:你好` | 有道自动译为英文 |
| `tr y:set:ja 你好` | 有道译为日文，手动目标优先 |
| `tr all:hello` | 并发调用全部启用的引擎，每家一行 |
| `tr all:你好` | 全部启用引擎同时译为英文，每家一行 |
| `tr set:en 你好` | 默认引擎译为英文；只对本次输入生效 |
| `tr set:zh 你好` | 手动指定中文，优先于自动中英互译 |
| `tr v:set:ja 你好` | 火山引擎译为日文 |
| `tr -- v:hello` | 将 `v:hello` 当作原文 |
| `tr v:-- set:en hello` | 火山将 `set:en hello` 当作原文 |
| `trconfig` | 首次自动导入已有 JSON 设置，再打开独立配置窗口 |
| `trconfig-native` | 打开 Alfred 原生配置面板，共享相同设置，作为备用入口 |
| `trconfig-json` | 打开用户 `config.json`；关闭面板模式后，JSON 设置生效 |

目标语言：`zh` 简体中文、`en` 英语、`ja` 日语、`ko` 韩语、`fr` 法语、`de` 德语、`ru` 俄语。未提供繁体语言选项。`set:` 后使用一个空格分隔原文；剩余空白、换行保留。空输入只给提示。

目标语言优先级为：**手动 `set:` → 自动中英互译 → 配置中的 `target_lang_default`**。未使用 `set:` 时：

- 中文（包括繁体和扩展汉字）自动译为英文；中英混合文本也译为英文。
- 仅含拉丁字母的文本按英文处理，自动译为简体中文；数字、标点和 emoji 不影响这个方向。
- 含假名、韩文、俄文等其他语言字母，或只有数字、标点、emoji 时，使用 `target_lang_default`。

上述判断在移除查询前缀后进行，只用于选择目标语言，不修改原文，也不增加语言检测 API 请求。服务商仍自动识别源语言。字符规则不能准确区分英语与法语等纯拉丁文字，也不能区分中文与纯汉字日文；有歧义时请用 `set:` 指定目标。默认、显式引擎、`all:` 和兜底都采用相同的方向选择规则。

前缀只在开头按上述顺序识别；URL、正文中的冒号不会被拆开。只有无引擎前缀时才兜底，显式指定和 `all:` 不会悄悄换引擎。关闭默认引擎时，无前缀调用在允许兜底的情况下会跳过它。

`all:` 的显示顺序沿用 `fallback_order`，其他启用引擎追加在后面；缺配置的引擎会显示独立错误。普通调用最多请求三个不同的服务商，跳过禁用/缺配置项；认证、额度、限流、网络、暂时服务故障可切换，输入问题不重试。成功结果会标明实际引擎与兜底来源。

输入延迟使用 Alfred 的自动延迟，首字符不立即请求，旧查询可终止。已经发送到云端的请求不能保证撤销计费；`all:` 与自动兜底可能消耗多家额度。

## 配置与数据

有两种配置来源，由「使用窗口配置」决定：

- **勾选**：翻译时使用原生面板字段，不读取或写入用户 JSON。缓存、超时和未在面板展示的选项使用 `config.example.json` 中的默认值。
- **未勾选（默认）**：使用用户 `config.json`，保留全部现有选项；面板中的厂商、密钥和兜底字段不会覆盖 JSON。`PYTHON_BINARY` 是两种模式共用的启动设置。

窗口和原生面板的值保存在同一个 `prefs.plist`，包括输入或导入的密钥；独立窗口保存时保留其他已有字段，原 JSON 不变。这是本地配置文件，遮罩不等于加密，也不是 Keychain。独立窗口默认隐藏密钥及代理地址，原生面板仍可能显示明文，请勿分享含私密内容的截图或配置文件。`prefs.plist` 已加入忽略规则，打包器只取白名单文件，不会打包它。

发行默认值不含真实密钥。在 JSON 模式首次使用或执行 `trconfig-json` 时，以权限 0600 创建用户 `config.json`，之后绝不覆盖它。其位置通过 `alfred_workflow_data` 获取；缓存和日志通过 `alfred_workflow_cache` 获取。未在 Alfred 内运行时，按当前用户主目录动态定位对应数据目录。Bundle ID 保持 `local.multitranslate.alfred`。

### 从已有 JSON 自动导入

1. 更新 Workflow 后，在 Alfred 搜索浮窗输入 **`trconfig`** 并回车。程序从用户数据目录现有的 `config.json` 导入，再打开面板；不需要复制粘贴密钥。
2. 核对默认服务商、启用开关、密钥和地域。**面板已保存的字段优先**，即使值为空或关闭，也不会被 JSON 覆盖；没有保存过的字段才自动填入。
3. 如需切换到面板管理，勾选 **使用窗口配置** 并保存。导入本身不改变这个开关；取消勾选仍可使用原 JSON。

导入内容为五家服务商的启用状态、全部密钥字段、适用的地域、代理设置，以及默认服务商、自动兜底开关、兜底顺序和全局代理。JSON 中删除的服务商会保持关闭。Python 解释器设置不受影响；缓存、超时、日志、回落目标语言等高级选项不导入，继续使用这些自定义项请保持 JSON 模式。

导入成功后在用户数据目录保存不含密钥的 `panel-import-v1.json` 标记，只自动导入一次。以后在面板清空密钥或恢复默认值，再运行 `trconfig` 也不会从 JSON 填回；修改 JSON 不会实时同步到面板。第一次直接点击 **Configure Workflow…** 不触发导入，先运行一次 `trconfig` 即可，之后两种入口均可使用。

没有 JSON 时不创建配置或导入标记。JSON 格式错误、包含面板尚未支持的服务商、面板配置损坏或正在被其他进程改动时，会停止导入，保留原文件；仍可打开面板检查，或用 `trconfig-json` 修正 JSON 后重试。写入或刷新失败会尝试恢复原面板；不会自动启用导入后的配置。导入只处理本地文件，不调用翻译 API，也不会把密钥放入命令行或日志。

### 窗口与原生面板共享的字段

除 Python 解释器外，下列变量只在面板模式启用时使用。复选框由 Alfred 传入字符串 `0` 或 `1`，代码会进行严格校验；不允许把字符串 `"0"` 当作开启。

| 面板字段 / 变量 | 用途、默认值与约束 |
| --- | --- |
| 使用窗口配置 / `MT_USE_PANEL` | 默认关闭（0），保持现有 JSON 生效；勾选为 1 |
| 默认服务商 / `MT_DEFAULT_ENGINE` | 默认 `volc`，下拉可选 `volc / baidu / aliyun / azure / youdao`；对应服务商必须另行启用并填写密钥 |
| 自动兜底 / `MT_FALLBACK_ENABLED` | 默认开启（1）；仅无服务商前缀的请求使用 |
| 服务商顺序 / `MT_FALLBACK_ORDER` | 用于兜底与 all 对比；默认 `volc,azure,aliyun,baidu,youdao`；逗号分隔上述 ID，空格会清理；留空则不追加兜底候选；保留已保存的顺序 |
| 全局代理模式 / `MT_PROXY_MODE` | 默认 `system`（跟随系统）；可选 `direct`（直连）、`custom`（指定代理） |
| 全局代理地址 / `MT_PROXY_URL` | 默认空；仅全局 `custom` 时必填，只支持 HTTP 代理 URL，可带编码后的认证信息 |
| 厂商代理模式 / `MT_<ENGINE>_PROXY_MODE` | 默认 `inherit`（继承全局）；可单独设为 `system`、`direct` 或 `custom`。ENGINE 为 `VOLC / BAIDU / ALIYUN / AZURE / YOUDAO` |
| 厂商代理地址 / `MT_<ENGINE>_PROXY_URL` | 默认空；仅该厂商 `custom` 时必填，使用全局同样的地址格式；其余模式忽略此字段 |
| 五家启用开关 / `MT_VOLC_ENABLED`、`MT_BAIDU_ENABLED`、`MT_ALIYUN_ENABLED`、`MT_AZURE_ENABLED`、`MT_YOUDAO_ENABLED` | 均默认关闭（0）；只勾选实际要使用的厂商，启用后参与指定调用和 all 对比 |
| 火山密钥 / `MT_VOLC_AK`、`MT_VOLC_SK` | 默认空，启用火山后填写对应 AccessKey ID、SecretKey |
| 火山地域 / `MT_VOLC_REGION` | 默认并要求 `cn-north-1` |
| 百度密钥 / `MT_BAIDU_APPID`、`MT_BAIDU_APPKEY` | 默认空，启用百度后填写翻译开放平台 AppID 和密钥 |
| 阿里云密钥 / `MT_ALIYUN_AK`、`MT_ALIYUN_SK` | 默认空，启用阿里云后填写 AccessKey ID、AccessKey Secret |
| 阿里云地域 / `MT_ALIYUN_REGION` | 默认并要求 `cn-hangzhou` |
| Azure 密钥 / `MT_AZURE_KEY` | 默认空，启用 Azure 后填写 Translator 订阅密钥 |
| Azure 地域 / `MT_AZURE_REGION` | 默认空用于全球资源；区域资源必须填写其实际地域 |
| 有道应用 ID / `MT_YOUDAO_APP_KEY` | 默认空，启用有道后必填；对应 API 的 `appKey`，在有道智云应用管理获取 |
| 有道应用密钥 / `MT_YOUDAO_APP_SECRET` | 默认空，启用有道后必填；与应用 ID 配对参与 v3 签名，不填写地域 |
| Python 解释器 / `PYTHON_BINARY` | 默认空，从 PATH 查找；必要时填入用户自己的 Python 3.10+ 解释器位置 |

清空面板密钥会立即使该厂商缺少凭据，不会偷偷回退到 JSON 中的旧密钥。需要恢复 JSON 设置时，只要取消「使用窗口配置」。

### JSON 模式字段

JSON 不能写注释，下面是字段说明；删去可选全局字段会使用模板默认值。引擎对象缺少字段时使用该引擎的默认字段，删除整个引擎对象则不再启用它。

| 字段 | 用途、默认值与约束 |
| --- | --- |
| `default_engine` | 无前缀调用的首选引擎，默认 `volc`；取已安装适配器 ID |
| `fallback_enable` | 是否为无前缀调用启用兜底，默认 `true`；布尔值 |
| `fallback_order` | 兜底及 all 的展示优先级，默认 `volc, azure, aliyun, baidu, youdao`；字符串数组，重复项自动去重；旧配置保持原顺序 |
| `proxy.mode` | 全局代理，默认 `system`；可选 `system / direct / custom`，不接受 `inherit` |
| `proxy.url` | 默认空；全局 `custom` 时必填 HTTP 代理地址，其他模式保留但忽略 |
| `target_lang_default` | 未使用 set 且无法按上述字符规则选择中英互译方向时的回落目标，默认 `zh`；取上述七种语言。修改此字段不会关闭自动中英互译 |
| `request_timeout_seconds` | 单家请求等待上限，默认 10 秒，范围 0.1–10 |
| `query_timeout_seconds` | 整次查询的调度等待预算，默认 15 秒，范围 0.1–60；各请求同时受剩余预算约束 |
| `cache_enabled` | 成功结果缓存开关，默认 `true` |
| `cache_ttl_seconds` | 缓存有效期，默认 300 秒，范围 1–3600 |
| `cache_max_text_chars` | 可缓存原文长度，默认 1000 字符，整数 1–50000；更长文本仍可翻译 |
| `cache_max_entries` | 缓存条目上限，默认 256，整数 1–1000；同时有 2 MiB 文件上限 |
| `log_enabled` | 诊断日志开关，默认 `false`；布尔值 |
| `engines.*.enable` | 单个引擎是否参与调用；模板中原有四家为 `true`，新增有道为 `false`；未填写密钥不会发请求 |
| `engines.*.proxy_mode` | 默认 `inherit`；可选 `inherit / system / direct / custom`，独立覆盖全局 |
| `engines.*.proxy_url` | 默认空；厂商 `custom` 时必填 HTTP 代理 URL，否则不使用 |
| `engines.volc.ak / sk` | 火山 AccessKey ID / Secret，默认空，启用调用时必填 |
| `engines.volc.region` | 火山 TranslateText 签名地域，默认并要求 `cn-north-1` |
| `engines.baidu.appid / appkey` | 百度翻译开放平台应用 ID / 密钥，默认空，启用调用时必填 |
| `engines.aliyun.ak / sk` | 阿里云 AccessKey ID / Secret，默认空，启用调用时必填 |
| `engines.aliyun.region` | 当前使用杭州接入点，默认并要求 `cn-hangzhou` |
| `engines.azure.key` | Azure Translator 订阅密钥，默认空，启用调用时必填 |
| `engines.azure.region` | 默认空，适用于全球资源；区域或多服务资源必须填写实际区域，如 `eastasia`，不能随意套用 |
| `engines.youdao.app_key` | 有道应用 ID（API 字段 `appKey`），默认空，启用有道时必填 |
| `engines.youdao.app_secret` | 有道应用密钥，默认空，与应用 ID 配对用于 SHA256 v3 签名；不是请求中的 `appKey` |
| Workflow 环境变量 `PYTHON_BINARY` | 默认空，使用 PATH 中的 python3；需要时由用户提供解释器位置，不是 JSON 字段 |

缓存键隔离服务商、原文、源/目标语言与缓存格式版本。只保存短文本的成功结果，不保存密钥、认证头或失败；JSON 有跨进程锁和原子替换，损坏或忙时退化为未命中。缓存含原文与译文，需要时可以删除 `translations.json` 清除。日志名为 `workflow.log`，最多一个备份，每个文件 256 KiB；仅记录引擎、错误码、HTTP 状态、请求 ID、耗时和缓存状态。

升级到 1.1.0 后无需修改已有密钥配置，也无需手动清空缓存：目标语言属于缓存键，中文自动译英文不会复用此前中文译中文的结果。重新导入新版 `.alfredworkflow` 并替换同一 Workflow 即可使用新行为。

超过服务商单次上限时不截断、不发送：火山、阿里和有道 5000 字符，百度 6000 UTF-8 字节，Azure 50000 字符。`all:` 中各家仍使用同一份原文，超限项独立报错。

## 申请密钥与计费

以下官方资料核对于 2026-11-05；优惠、免费额度、套餐和地域可能调整，以你账号的实际控制台为准。本项目不保证免费，也不自动充值。

- 火山：在[火山控制台](https://console.volcengine.com/)开通机器翻译，获取有调用权限的 AccessKey。[接口](https://docs.volcengine.com/docs/MachineTranslation/TextTranslationAPI?lang=zh)、[计费](https://docs.volcengine.com/docs/MachineTranslation/Productbilling?lang=zh)。本适配器调用 TranslateText 2020-06-01，不是豆包翻译模型。
- 百度：在[百度翻译开放平台](https://fanyi-api.baidu.com/)开通通用文本翻译，取得 AppID 和密钥。[通用接口及错误码](https://fanyi-api.baidu.com/doc_bd/21)。不要使用百度智能云其他产品的 AK/SK 代替。
- 阿里云：在[阿里云控制台](https://home.console.aliyun.com/)开通机器翻译通用版，为账号授予调用权限并获取 AccessKey。[TranslateGeneral](https://help.aliyun.com/zh/machine-translation/developer-reference/api-alimt-2018-10-12-translategeneral)、[V3 签名](https://help.aliyun.com/zh/sdk/product-overview/v3-request-structure-and-signature)。不是百炼 Qwen-MT API Key。
- Azure：在[Azure Portal](https://portal.azure.com/)创建 Translator 资源，在 Keys and Endpoint 页面获取密钥及地域。[认证](https://learn.microsoft.com/en-us/azure/ai-services/translator/text-translation/reference/authentication)、[价格](https://azure.microsoft.com/en-us/pricing/details/cognitive-services/translator/)。本实现是全球公有云 Translator 3.0；不覆盖中国云、专用网络、自定义域名或 token 鉴权。
- 有道：在[有道智云控制台](https://ai.youdao.com/console/)开通文本翻译服务并绑定应用，取得应用 ID 和应用密钥。[文本翻译 API](https://ai.youdao.com/DOCSIRMA/html/trans/api/wbfy/index.html)、[计费说明](https://ai.youdao.com/DOCSIRMA/html/trans/price/wbfy/index.html)。使用通用文本翻译接口；体验额度、有效期和账单以账号为准，不保证永久免费。

## 签名与错误排查

- 火山：UTC 时间 `X-Date`、签名地域和 service 必须一致；签名的是实际 UTF-8 请求体字节，不能签完再改 JSON。HMAC 派生顺序是日期、地域、服务、`request`，不添加其他服务商的密钥前缀。[官方签名源码](https://raw.githubusercontent.com/volcengine/volc-sdk-python/main/volcengine/auth/SignerV4.py)。官方未穷举额度错误，本实现仅对明确表示余额/额度耗尽的消息做保守识别。
- 百度：MD5 输入为 `appid + 原文 + salt + appkey`；先签原文，再表单编码。日语 `jp`、韩语 `kor`、法语 `fra` 由适配器转换。`54001` 是签名错误，`52003` 是未授权。多段结果全部连接，不只取第一段。
- 阿里云：使用 `ACS3-HMAC-SHA256`；表单请求体的编码、SHA256 摘要、规范头和实际发送字节必须一致；`content-type` 也参加签名。`10013` 同时可能表示未开通或欠费，不能武断显示为免费额度用尽。
- Azure：简体中文用 `zh-Hans`，自动源语言省略 `from`；全球与区域资源的 header 要匹配。`403001` 映射额度问题，不能将所有 403 当密钥错误。
- 有道：使用 `signType=v3`；原文不超过 20 字符时直接参与签名，超过时用前 10 字符、字符数、后 10 字符拼接，之后再做 UTF-8 表单编码；实际发送的 `q` 不截断。每次生成新 salt 和秒级 UTC 时间戳。显式发送 `strict=true`，防止服务端自动换向覆盖手动 `set:`。`202` 为签名错误、`206` 为时间戳错误、`207` 为重放、`401` 为账户欠费、`411/412` 为限流；HTTP 401 与业务码 401 含义不同。[官方说明](https://ai.youdao.com/DOCSIRMA/html/trans/api/wbfy/index.html)。

所有 API 统一返回 success、engine、original_text、translated_text、from_lang、to_lang、raw_error、error_code；另含 vendor_code、request_id、http_status、cached、fallback_from、elapsed_ms。未知源语言用 `und`。`raw_error` 是安全的中文说明，不保留可能回显正文/密钥的原始服务商消息；具体码和请求 ID 用于追查。失败不修改剪贴板。

## 项目文件

```text
main.py                 入口、配置引导、日志初始化
config.py               配置读取和用户目录
panel_import.py         已有 JSON 一次性导入原生配置面板
settings_window.py      窗口数据、校验、原子保存及失败恢复
config_window.swift     AppKit 独立窗口源码与真正全宽分隔
config_window           已编译的 Intel / Apple Silicon 通用程序
config.example.json     无密钥模板
models.py / parser.py   统一数据结构与前缀解析
scheduler.py            默认、指定、并发及兜底调用
cache.py                五分钟 JSON 缓存
http_client.py          urllib 传输、系统/直连/独立代理
output.py / utils.py    Alfred 输出与小型编码函数
adapters/
  __init__.py / base.py  自动发现与统一适配器协议
  volc.py / baidu.py / aliyun.py / azure.py / youdao.py
bootstrap.sh            解释器检查与 argv 透传
info.plist              已连好的 Alfred 对象
build_workflow.py       编译原生窗口并按白名单打包
tests/                  离线功能验证
VALIDATION.md           实际验证记录及未验证范围
```

## 手工搭建与开发

已有 `info.plist` 可以直接导入安装包。手工搭建时：

1. 新建空 Workflow，Bundle ID 设为 `local.multitranslate.alfred`，将运行文件放入 Workflow 目录。
2. 添加 Script Filter：关键词 `tr`、Argument Optional、关闭 Alfred filters results、语言选 bash、输入方式选 argv；脚本为 `exec bash ./bootstrap.sh translate "$@"`。
3. Run Behaviour 选择终止前一个脚本、输入停止后自动延迟，关闭首字符立即执行；保留原始空白。
4. 连接 Copy to Clipboard：使用传入文本（文本框留空），关闭自动粘贴、开启忽略动态占位符。这样原文中的 `{date}` 等不会被再次展开。
5. 添加无参数 Keyword `trconfig`，连接 Run Script，脚本为 `exec bash ./bootstrap.sh configure`，自动导入已有 JSON 后打开独立窗口。备用 Keyword `trconfig-native` 连接 `exec bash ./bootstrap.sh configure-native`，打开原生面板；`trconfig-json` 连接 `exec bash ./bootstrap.sh configure-json`，打开 JSON 文件。
6. 原生面板定义保存在 `info.plist` 的 `userconfigurationconfig`；字段默认值在发行包中均不含密钥，用户填写的值由 Alfred 作为环境变量传给脚本。

安装包日常使用无需编译。自行修改窗口或重新打包时，需要本机已有 Xcode 命令行工具（`xcrun`、`swiftc`、`lipo`、`codesign`）；打包脚本不会自动安装工具或依赖。窗口为 macOS 11+ 的双架构程序，采用本地 ad-hoc 签名，不代表 Apple 公证。

在原项目根目录执行打包与离线功能验证（不访问云端、不安装 Workflow）：

```bash
python3 -B build_workflow.py
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -v
```

测试临时数据只在项目内创建并清理；测试失败输出应先脱敏再落盘。lint 和其他独立检查需另行授权。安装包白名单排除测试、计划、真实配置、日志、缓存和字节码；不生成中间项目副本。

代理专项测试使用本地 HTTP 服务器；HTTPS 隧道用本机已有 OpenSSL 命令临时生成证书，不修改系统信任。没有 OpenSSL 时该项会明确跳过，不能视为已验证 TLS。Workflow 的日常运行不需要 OpenSSL 命令。

## 增加或删除服务商

在 `adapters/` 新增一个 Python 文件，导出继承 `BaseAdapter` 的 `Adapter` 类，声明唯一的 `engine/name/prefix`、`required_fields`、`language_map`、长度约束，并实现 `build_request` 和 `parse_response`。`translate`、网络异常保护和通用输出由现有协议复用，不要在适配器打印 JSON。

在 JSON 模式的 `engines` 添加对应密钥与 `enable`，需要时调整 `fallback_order/default_engine`，然后重新打包；无需修改主调度逻辑。原生面板当前提供上述五家字段，新服务商可先使用 JSON 模式。移除服务商时删除适配器及相应配置项，避免将它留作默认引擎。新增适配器必须像现有实现一样保留 TLS 校验，不能引入跨域重定向或记录认证信息。

## 验证范围与移除

离线成功不等于账号接入成功。真实 API、计费、Alfred 导入与回车复制需要在你的实际环境完成验证；具体已执行项目见项目根目录 `VALIDATION.md`。不应根据代码或截图声称已通过真实接入。

移除时在 Alfred 删除本 Workflow 即停止使用。用户数据不会自动删除，以免丢失密钥；可由用户自行清理本 Bundle ID 的数据与缓存。
