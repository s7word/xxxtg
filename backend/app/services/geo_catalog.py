"""全球 ISO-2 拓扑推断引擎。

接码平台动态返回任意国家 ID 时，本模块负责把 ISO-2 映射为：
国际区号、中英文国名、国旗 Emoji、主流 BCP-47 语言、UTC 时区偏置。

这是标准地理知识库，不是写死「只支持 N 个国家」的业务白名单。
未收录的合法 ISO-2 也会按启发式合成一组自洽的语言 / 时区参数。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

# iso2 -> (iso3, name_en, name_zh, dial, lang, system_lang, tz_offset_sec, region)
# region: na / sa / eu / cis / me / af / apac
_ISO2_CORE: Dict[str, Tuple[str, str, str, str, str, str, int, str]] = {
    # 北美
    "us": ("USA", "United States", "美国", "1", "en", "en-us", -18000, "na"),
    "ca": ("CAN", "Canada", "加拿大", "1", "en", "en-ca", -18000, "na"),
    "mx": ("MEX", "Mexico", "墨西哥", "52", "es", "es-mx", -21600, "na"),
    "gt": ("GTM", "Guatemala", "危地马拉", "502", "es", "es-gt", -21600, "na"),
    "hn": ("HND", "Honduras", "洪都拉斯", "504", "es", "es-hn", -21600, "na"),
    "sv": ("SLV", "El Salvador", "萨尔瓦多", "503", "es", "es-sv", -21600, "na"),
    "ni": ("NIC", "Nicaragua", "尼加拉瓜", "505", "es", "es-ni", -21600, "na"),
    "cr": ("CRI", "Costa Rica", "哥斯达黎加", "506", "es", "es-cr", -21600, "na"),
    "pa": ("PAN", "Panama", "巴拿马", "507", "es", "es-pa", -18000, "na"),
    "bz": ("BLZ", "Belize", "伯利兹", "501", "en", "en-bz", -21600, "na"),
    "cu": ("CUB", "Cuba", "古巴", "53", "es", "es-cu", -18000, "na"),
    "do": ("DOM", "Dominican Republic", "多米尼加", "1809", "es", "es-do", -14400, "na"),
    "ht": ("HTI", "Haiti", "海地", "509", "fr", "fr-ht", -18000, "na"),
    "jm": ("JAM", "Jamaica", "牙买加", "1876", "en", "en-jm", -18000, "na"),
    "tt": ("TTO", "Trinidad and Tobago", "特立尼达和多巴哥", "1868", "en", "en-tt", -14400, "na"),
    "bb": ("BRB", "Barbados", "巴巴多斯", "1246", "en", "en-bb", -14400, "na"),
    "bs": ("BHS", "Bahamas", "巴哈马", "1242", "en", "en-bs", -18000, "na"),
    "pr": ("PRI", "Puerto Rico", "波多黎各", "1787", "es", "es-pr", -14400, "na"),
    "ag": ("ATG", "Antigua and Barbuda", "安提瓜和巴布达", "1268", "en", "en-ag", -14400, "na"),
    "lc": ("LCA", "Saint Lucia", "圣卢西亚", "1758", "en", "en-lc", -14400, "na"),
    "vc": ("VCT", "Saint Vincent", "圣文森特", "1784", "en", "en-vc", -14400, "na"),
    "gd": ("GRD", "Grenada", "格林纳达", "1473", "en", "en-gd", -14400, "na"),
    "kn": ("KNA", "Saint Kitts and Nevis", "圣基茨和尼维斯", "1869", "en", "en-kn", -14400, "na"),
    "dm": ("DMA", "Dominica", "多米尼克", "1767", "en", "en-dm", -14400, "na"),
    "ky": ("CYM", "Cayman Islands", "开曼群岛", "1345", "en", "en-ky", -18000, "na"),
    "aw": ("ABW", "Aruba", "阿鲁巴", "297", "nl", "nl-aw", -14400, "na"),
    "gp": ("GLP", "Guadeloupe", "瓜德罗普", "590", "fr", "fr-gp", -14400, "na"),
    # 南美
    "cl": ("CHL", "Chile", "智利", "56", "es", "es-cl", -14400, "sa"),
    "br": ("BRA", "Brazil", "巴西", "55", "pt", "pt-br", -10800, "sa"),
    "co": ("COL", "Colombia", "哥伦比亚", "57", "es", "es-co", -18000, "sa"),
    "pe": ("PER", "Peru", "秘鲁", "51", "es", "es-pe", -18000, "sa"),
    "ar": ("ARG", "Argentina", "阿根廷", "54", "es", "es-ar", -10800, "sa"),
    "ec": ("ECU", "Ecuador", "厄瓜多尔", "593", "es", "es-ec", -18000, "sa"),
    "bo": ("BOL", "Bolivia", "玻利维亚", "591", "es", "es-bo", -14400, "sa"),
    "py": ("PRY", "Paraguay", "巴拉圭", "595", "es", "es-py", -14400, "sa"),
    "uy": ("URY", "Uruguay", "乌拉圭", "598", "es", "es-uy", -10800, "sa"),
    "ve": ("VEN", "Venezuela", "委内瑞拉", "58", "es", "es-ve", -14400, "sa"),
    "gy": ("GUY", "Guyana", "圭亚那", "592", "en", "en-gy", -14400, "sa"),
    "sr": ("SUR", "Suriname", "苏里南", "597", "nl", "nl-sr", -10800, "sa"),
    "gf": ("GUF", "French Guiana", "法属圭亚那", "594", "fr", "fr-gf", -10800, "sa"),
    # 西欧 / 北欧 / 南欧
    "gb": ("GBR", "United Kingdom", "英国", "44", "en", "en-gb", 0, "eu"),
    "ie": ("IRL", "Ireland", "爱尔兰", "353", "en", "en-ie", 0, "eu"),
    "de": ("DEU", "Germany", "德国", "49", "de", "de-de", 3600, "eu"),
    "fr": ("FRA", "France", "法国", "33", "fr", "fr-fr", 3600, "eu"),
    "es": ("ESP", "Spain", "西班牙", "34", "es", "es-es", 3600, "eu"),
    "pt": ("PRT", "Portugal", "葡萄牙", "351", "pt", "pt-pt", 0, "eu"),
    "it": ("ITA", "Italy", "意大利", "39", "it", "it-it", 3600, "eu"),
    "nl": ("NLD", "Netherlands", "荷兰", "31", "nl", "nl-nl", 3600, "eu"),
    "be": ("BEL", "Belgium", "比利时", "32", "nl", "nl-be", 3600, "eu"),
    "lu": ("LUX", "Luxembourg", "卢森堡", "352", "fr", "fr-lu", 3600, "eu"),
    "mc": ("MCO", "Monaco", "摩纳哥", "377", "fr", "fr-mc", 3600, "eu"),
    "at": ("AUT", "Austria", "奥地利", "43", "de", "de-at", 3600, "eu"),
    "ch": ("CHE", "Switzerland", "瑞士", "41", "de", "de-ch", 3600, "eu"),
    "se": ("SWE", "Sweden", "瑞典", "46", "sv", "sv-se", 3600, "eu"),
    "no": ("NOR", "Norway", "挪威", "47", "no", "nb-no", 3600, "eu"),
    "dk": ("DNK", "Denmark", "丹麦", "45", "da", "da-dk", 3600, "eu"),
    "fi": ("FIN", "Finland", "芬兰", "358", "fi", "fi-fi", 7200, "eu"),
    "is": ("ISL", "Iceland", "冰岛", "354", "is", "is-is", 0, "eu"),
    "pl": ("POL", "Poland", "波兰", "48", "pl", "pl-pl", 3600, "eu"),
    "cz": ("CZE", "Czechia", "捷克", "420", "cs", "cs-cz", 3600, "eu"),
    "sk": ("SVK", "Slovakia", "斯洛伐克", "421", "sk", "sk-sk", 3600, "eu"),
    "hu": ("HUN", "Hungary", "匈牙利", "36", "hu", "hu-hu", 3600, "eu"),
    "ro": ("ROU", "Romania", "罗马尼亚", "40", "ro", "ro-ro", 7200, "eu"),
    "bg": ("BGR", "Bulgaria", "保加利亚", "359", "bg", "bg-bg", 7200, "eu"),
    "gr": ("GRC", "Greece", "希腊", "30", "el", "el-gr", 7200, "eu"),
    "cy": ("CYP", "Cyprus", "塞浦路斯", "357", "el", "el-cy", 7200, "eu"),
    "mt": ("MLT", "Malta", "马耳他", "356", "en", "en-mt", 3600, "eu"),
    "si": ("SVN", "Slovenia", "斯洛文尼亚", "386", "sl", "sl-si", 3600, "eu"),
    "hr": ("HRV", "Croatia", "克罗地亚", "385", "hr", "hr-hr", 3600, "eu"),
    "ba": ("BIH", "Bosnia and Herzegovina", "波黑", "387", "bs", "bs-ba", 3600, "eu"),
    "rs": ("SRB", "Serbia", "塞尔维亚", "381", "sr", "sr-rs", 3600, "eu"),
    "me": ("MNE", "Montenegro", "黑山", "382", "sr", "sr-me", 3600, "eu"),
    "mk": ("MKD", "North Macedonia", "北马其顿", "389", "mk", "mk-mk", 3600, "eu"),
    "al": ("ALB", "Albania", "阿尔巴尼亚", "355", "sq", "sq-al", 3600, "eu"),
    "lt": ("LTU", "Lithuania", "立陶宛", "370", "lt", "lt-lt", 7200, "eu"),
    "lv": ("LVA", "Latvia", "拉脱维亚", "371", "lv", "lv-lv", 7200, "eu"),
    "ee": ("EST", "Estonia", "爱沙尼亚", "372", "et", "et-ee", 7200, "eu"),
    "md": ("MDA", "Moldova", "摩尔多瓦", "373", "ro", "ro-md", 7200, "eu"),
    "by": ("BLR", "Belarus", "白俄罗斯", "375", "be", "be-by", 10800, "eu"),
    # CIS
    "ru": ("RUS", "Russia", "俄罗斯", "7", "ru", "ru-ru", 10800, "cis"),
    "ua": ("UKR", "Ukraine", "乌克兰", "380", "uk", "uk-ua", 7200, "cis"),
    "kz": ("KAZ", "Kazakhstan", "哈萨克斯坦", "7", "ru", "ru-kz", 18000, "cis"),
    "uz": ("UZB", "Uzbekistan", "乌兹别克斯坦", "998", "uz", "uz-uz", 18000, "cis"),
    "kg": ("KGZ", "Kyrgyzstan", "吉尔吉斯斯坦", "996", "ky", "ky-kg", 21600, "cis"),
    "tj": ("TJK", "Tajikistan", "塔吉克斯坦", "992", "tg", "tg-tj", 18000, "cis"),
    "tm": ("TKM", "Turkmenistan", "土库曼斯坦", "993", "tk", "tk-tm", 18000, "cis"),
    "am": ("ARM", "Armenia", "亚美尼亚", "374", "hy", "hy-am", 14400, "cis"),
    "az": ("AZE", "Azerbaijan", "阿塞拜疆", "994", "az", "az-az", 14400, "cis"),
    "ge": ("GEO", "Georgia", "格鲁吉亚", "995", "ka", "ka-ge", 14400, "cis"),
    # 中东
    "tr": ("TUR", "Turkey", "土耳其", "90", "tr", "tr-tr", 10800, "me"),
    "ae": ("ARE", "United Arab Emirates", "阿联酋", "971", "ar", "ar-ae", 14400, "me"),
    "sa": ("SAU", "Saudi Arabia", "沙特", "966", "ar", "ar-sa", 10800, "me"),
    "eg": ("EGY", "Egypt", "埃及", "20", "ar", "ar-eg", 7200, "me"),
    "il": ("ISR", "Israel", "以色列", "972", "he", "he-il", 7200, "me"),
    "ps": ("PSE", "Palestine", "巴勒斯坦", "970", "ar", "ar-ps", 7200, "me"),
    "jo": ("JOR", "Jordan", "约旦", "962", "ar", "ar-jo", 10800, "me"),
    "lb": ("LBN", "Lebanon", "黎巴嫩", "961", "ar", "ar-lb", 7200, "me"),
    "sy": ("SYR", "Syria", "叙利亚", "963", "ar", "ar-sy", 10800, "me"),
    "iq": ("IRQ", "Iraq", "伊拉克", "964", "ar", "ar-iq", 10800, "me"),
    "ir": ("IRN", "Iran", "伊朗", "98", "fa", "fa-ir", 12600, "me"),
    "kw": ("KWT", "Kuwait", "科威特", "965", "ar", "ar-kw", 10800, "me"),
    "qa": ("QAT", "Qatar", "卡塔尔", "974", "ar", "ar-qa", 10800, "me"),
    "bh": ("BHR", "Bahrain", "巴林", "973", "ar", "ar-bh", 10800, "me"),
    "om": ("OMN", "Oman", "阿曼", "968", "ar", "ar-om", 14400, "me"),
    "ye": ("YEM", "Yemen", "也门", "967", "ar", "ar-ye", 10800, "me"),
    "af": ("AFG", "Afghanistan", "阿富汗", "93", "en", "en-af", 16200, "me"),
    # 非洲
    "za": ("ZAF", "South Africa", "南非", "27", "en", "en-za", 7200, "af"),
    "ng": ("NGA", "Nigeria", "尼日利亚", "234", "en", "en-ng", 3600, "af"),
    "ke": ("KEN", "Kenya", "肯尼亚", "254", "en", "en-ke", 10800, "af"),
    "tz": ("TZA", "Tanzania", "坦桑尼亚", "255", "sw", "sw-tz", 10800, "af"),
    "ug": ("UGA", "Uganda", "乌干达", "256", "en", "en-ug", 10800, "af"),
    "et": ("ETH", "Ethiopia", "埃塞俄比亚", "251", "am", "am-et", 10800, "af"),
    "gh": ("GHA", "Ghana", "加纳", "233", "en", "en-gh", 0, "af"),
    "ci": ("CIV", "Ivory Coast", "科特迪瓦", "225", "fr", "fr-ci", 0, "af"),
    "sn": ("SEN", "Senegal", "塞内加尔", "221", "fr", "fr-sn", 0, "af"),
    "cm": ("CMR", "Cameroon", "喀麦隆", "237", "fr", "fr-cm", 3600, "af"),
    "ma": ("MAR", "Morocco", "摩洛哥", "212", "ar", "ar-ma", 3600, "af"),
    "dz": ("DZA", "Algeria", "阿尔及利亚", "213", "ar", "ar-dz", 3600, "af"),
    "tn": ("TUN", "Tunisia", "突尼斯", "216", "ar", "ar-tn", 3600, "af"),
    "ly": ("LBY", "Libya", "利比亚", "218", "ar", "ar-ly", 7200, "af"),
    "sd": ("SDN", "Sudan", "苏丹", "249", "ar", "ar-sd", 7200, "af"),
    "ss": ("SSD", "South Sudan", "南苏丹", "211", "en", "en-ss", 10800, "af"),
    "ao": ("AGO", "Angola", "安哥拉", "244", "pt", "pt-ao", 3600, "af"),
    "mz": ("MOZ", "Mozambique", "莫桑比克", "258", "pt", "pt-mz", 7200, "af"),
    "zw": ("ZWE", "Zimbabwe", "津巴布韦", "263", "en", "en-zw", 7200, "af"),
    "zm": ("ZMB", "Zambia", "赞比亚", "260", "en", "en-zm", 7200, "af"),
    "mw": ("MWI", "Malawi", "马拉维", "265", "en", "en-mw", 7200, "af"),
    "na": ("NAM", "Namibia", "纳米比亚", "264", "en", "en-na", 7200, "af"),
    "bw": ("BWA", "Botswana", "博茨瓦纳", "267", "en", "en-bw", 7200, "af"),
    "rw": ("RWA", "Rwanda", "卢旺达", "250", "en", "en-rw", 7200, "af"),
    "bi": ("BDI", "Burundi", "布隆迪", "257", "fr", "fr-bi", 7200, "af"),
    "mg": ("MDG", "Madagascar", "马达加斯加", "261", "fr", "fr-mg", 10800, "af"),
    "mu": ("MUS", "Mauritius", "毛里求斯", "230", "en", "en-mu", 14400, "af"),
    "sc": ("SYC", "Seychelles", "塞舌尔", "248", "en", "en-sc", 14400, "af"),
    "re": ("REU", "Reunion", "留尼汪", "262", "fr", "fr-re", 14400, "af"),
    "cd": ("COD", "DR Congo", "刚果金", "243", "fr", "fr-cd", 3600, "af"),
    "cg": ("COG", "Congo", "刚果布", "242", "fr", "fr-cg", 3600, "af"),
    "ga": ("GAB", "Gabon", "加蓬", "241", "fr", "fr-ga", 3600, "af"),
    "gq": ("GNQ", "Equatorial Guinea", "赤道几内亚", "240", "es", "es-gq", 3600, "af"),
    "td": ("TCD", "Chad", "乍得", "235", "fr", "fr-td", 3600, "af"),
    "cf": ("CAF", "Central African Republic", "中非", "236", "fr", "fr-cf", 3600, "af"),
    "ne": ("NER", "Niger", "尼日尔", "227", "fr", "fr-ne", 3600, "af"),
    "ml": ("MLI", "Mali", "马里", "223", "fr", "fr-ml", 0, "af"),
    "bf": ("BFA", "Burkina Faso", "布基纳法索", "226", "fr", "fr-bf", 0, "af"),
    "bj": ("BEN", "Benin", "贝宁", "229", "fr", "fr-bj", 3600, "af"),
    "tg": ("TGO", "Togo", "多哥", "228", "fr", "fr-tg", 0, "af"),
    "gn": ("GIN", "Guinea", "几内亚", "224", "fr", "fr-gn", 0, "af"),
    "gw": ("GNB", "Guinea-Bissau", "几内亚比绍", "245", "pt", "pt-gw", 0, "af"),
    "sl": ("SLE", "Sierra Leone", "塞拉利昂", "232", "en", "en-sl", 0, "af"),
    "lr": ("LBR", "Liberia", "利比里亚", "231", "en", "en-lr", 0, "af"),
    "gm": ("GMB", "Gambia", "冈比亚", "220", "en", "en-gm", 0, "af"),
    "mr": ("MRT", "Mauritania", "毛里塔尼亚", "222", "ar", "ar-mr", 0, "af"),
    "so": ("SOM", "Somalia", "索马里", "252", "so", "so-so", 10800, "af"),
    "dj": ("DJI", "Djibouti", "吉布提", "253", "fr", "fr-dj", 10800, "af"),
    "er": ("ERI", "Eritrea", "厄立特里亚", "291", "ti", "ti-er", 10800, "af"),
    "ls": ("LSO", "Lesotho", "莱索托", "266", "en", "en-ls", 7200, "af"),
    "sz": ("SWZ", "Eswatini", "斯威士兰", "268", "en", "en-sz", 7200, "af"),
    "st": ("STP", "Sao Tome and Principe", "圣多美和普林西比", "239", "pt", "pt-st", 0, "af"),
    "cv": ("CPV", "Cape Verde", "佛得角", "238", "pt", "pt-cv", -3600, "af"),
    "km": ("COM", "Comoros", "科摩罗", "269", "ar", "ar-km", 10800, "af"),
    # 亚太
    "in": ("IND", "India", "印度", "91", "en", "en-in", 19800, "apac"),
    "id": ("IDN", "Indonesia", "印尼", "62", "id", "id-id", 25200, "apac"),
    "cn": ("CHN", "China", "中国", "86", "zh", "zh-cn", 28800, "apac"),
    "hk": ("HKG", "Hong Kong", "香港", "852", "zh", "zh-hk", 28800, "apac"),
    "mo": ("MAC", "Macau", "澳门", "853", "zh", "zh-mo", 28800, "apac"),
    "tw": ("TWN", "Taiwan", "台湾", "886", "zh", "zh-tw", 28800, "apac"),
    "jp": ("JPN", "Japan", "日本", "81", "ja", "ja-jp", 32400, "apac"),
    "kr": ("KOR", "South Korea", "韩国", "82", "ko", "ko-kr", 32400, "apac"),
    "th": ("THA", "Thailand", "泰国", "66", "th", "th-th", 25200, "apac"),
    "vn": ("VNM", "Vietnam", "越南", "84", "vi", "vi-vn", 25200, "apac"),
    "ph": ("PHL", "Philippines", "菲律宾", "63", "en", "en-ph", 28800, "apac"),
    "my": ("MYS", "Malaysia", "马来西亚", "60", "en", "en-my", 28800, "apac"),
    "sg": ("SGP", "Singapore", "新加坡", "65", "en", "en-sg", 28800, "apac"),
    "au": ("AUS", "Australia", "澳大利亚", "61", "en", "en-au", 36000, "apac"),
    "nz": ("NZL", "New Zealand", "新西兰", "64", "en", "en-nz", 43200, "apac"),
    "pk": ("PAK", "Pakistan", "巴基斯坦", "92", "en", "en-pk", 18000, "apac"),
    "bd": ("BGD", "Bangladesh", "孟加拉", "880", "bn", "bn-bd", 21600, "apac"),
    "lk": ("LKA", "Sri Lanka", "斯里兰卡", "94", "si", "si-lk", 19800, "apac"),
    "np": ("NPL", "Nepal", "尼泊尔", "977", "ne", "ne-np", 20700, "apac"),
    "mm": ("MMR", "Myanmar", "缅甸", "95", "my", "my-mm", 23400, "apac"),
    "kh": ("KHM", "Cambodia", "柬埔寨", "855", "km", "km-kh", 25200, "apac"),
    "la": ("LAO", "Laos", "老挝", "856", "lo", "lo-la", 25200, "apac"),
    "mn": ("MNG", "Mongolia", "蒙古", "976", "mn", "mn-mn", 28800, "apac"),
    "kp": ("PRK", "North Korea", "朝鲜", "850", "ko", "ko-kp", 32400, "apac"),
    "bn": ("BRN", "Brunei", "文莱", "673", "ms", "ms-bn", 28800, "apac"),
    "tl": ("TLS", "Timor-Leste", "东帝汶", "670", "pt", "pt-tl", 32400, "apac"),
    "pg": ("PNG", "Papua New Guinea", "巴布亚新几内亚", "675", "en", "en-pg", 36000, "apac"),
    "fj": ("FJI", "Fiji", "斐济", "679", "en", "en-fj", 43200, "apac"),
    "nc": ("NCL", "New Caledonia", "新喀里多尼亚", "687", "fr", "fr-nc", 39600, "apac"),
    "bt": ("BTN", "Bhutan", "不丹", "975", "dz", "dz-bt", 21600, "apac"),
    "mv": ("MDV", "Maldives", "马尔代夫", "960", "dv", "dv-mv", 18000, "apac"),
}

# 双语 / 多时区覆盖，不改变核心元组的紧凑结构。
_ISO2_EXTRAS: Dict[str, Dict[str, Any]] = {
    "ca": {"alt_system_lang_codes": ("fr-ca",), "tz_offset_range": (-28800, -14400)},
    "us": {"tz_offset_range": (-28800, -14400)},
    "mx": {"tz_offset_range": (-25200, -18000)},
    "au": {"tz_offset_range": (28800, 39600)},
    "ru": {"tz_offset_range": (7200, 43200)},
    "kz": {"alt_system_lang_codes": ("kk-kz",)},
    "be": {"alt_system_lang_codes": ("fr-be", "de-be")},
    "ch": {"alt_system_lang_codes": ("fr-ch", "it-ch")},
    "ma": {"alt_system_lang_codes": ("fr-ma",)},
    "dz": {"alt_system_lang_codes": ("fr-dz",)},
    "tn": {"alt_system_lang_codes": ("fr-tn",)},
    "tz": {"alt_system_lang_codes": ("en-tz",)},
    "cy": {"alt_system_lang_codes": ("tr-cy", "en-cy")},
    "il": {"alt_system_lang_codes": ("en-il", "ar-il")},
    "in": {"alt_system_lang_codes": ("hi-in",)},
    "ph": {"alt_system_lang_codes": ("fil-ph",)},
    "my": {"alt_system_lang_codes": ("ms-my",)},
    "sg": {"alt_system_lang_codes": ("zh-sg", "ms-sg")},
    "hk": {"alt_system_lang_codes": ("en-hk",)},
    "af": {"alt_system_lang_codes": ("fa-af", "ps-af")},
    "ua": {"alt_system_lang_codes": ("ru-ua",)},
    "by": {"alt_system_lang_codes": ("ru-by",)},
    "cm": {"alt_system_lang_codes": ("en-cm",)},
    "za": {"alt_system_lang_codes": ("af-za", "zu-za")},
}

# 额外别名（中文简称 / 英文俗称 / 历史拼写），自动索引之外的人工补充。
_EXTRA_NAME_ALIASES: Dict[str, str] = {
    "uk": "gb",
    "britain": "gb",
    "england": "gb",
    "great britain": "gb",
    "united kingdom": "gb",
    "usa": "us",
    "america": "us",
    "united states": "us",
    "uae": "ae",
    "ivory coast": "ci",
    "cote divoire": "ci",
    "côte d'ivoire": "ci",
    "czech republic": "cz",
    "czechia": "cz",
    "korea": "kr",
    "south korea": "kr",
    "holland": "nl",
    "burma": "mm",
    "印尼": "id",
    "印度尼西亚": "id",
    "英国": "gb",
    "美国": "us",
    "沙特": "sa",
    "沙特阿拉伯": "sa",
    "阿联酋": "ae",
    "香港": "hk",
    "台湾": "tw",
    "澳门": "mo",
}

REGION_LABELS = {
    "na": "北美 · North America",
    "sa": "南美 · South America",
    "eu": "欧洲 · Europe",
    "cis": "东欧 / CIS",
    "me": "中东 · Middle East",
    "af": "非洲 · Africa",
    "apac": "亚太 · Asia-Pacific",
}


def _normalize_token(value: Any) -> str:
    return re.sub(r"[_\-]+", " ", str(value or "").strip().lower()).strip()


def iso2_flag(code: Optional[str]) -> str:
    iso = str(code or "").strip().upper()
    if len(iso) != 2 or not iso.isalpha():
        return "🏳️"
    return chr(127397 + ord(iso[0])) + chr(127397 + ord(iso[1]))


def lookup_country(code: Optional[str]) -> Optional[Dict[str, Any]]:
    token = str(code or "").strip().lower()
    if token == "uk":
        token = "gb"
    row = _ISO2_CORE.get(token)
    if not row:
        return None
    iso3, name_en, name_zh, dial, lang, system_lang, tz_offset, region = row
    extras = _ISO2_EXTRAS.get(token) or {}
    return {
        "code": token,
        "iso2": token,
        "iso3": iso3,
        "name": name_en,
        "name_en": name_en,
        "name_zh": name_zh,
        "dial": dial,
        "flag": iso2_flag(token),
        "lang_code": extras.get("lang_code") or lang,
        "system_lang_code": extras.get("system_lang_code") or system_lang,
        "alt_system_lang_codes": tuple(extras.get("alt_system_lang_codes") or ()),
        "tz_offset": int(extras.get("tz_offset") or tz_offset),
        "tz_offset_range": extras.get("tz_offset_range"),
        "region": extras.get("region") or region,
    }


def iter_catalog() -> Iterable[Dict[str, Any]]:
    for code in _ISO2_CORE:
        item = lookup_country(code)
        if item:
            yield item


def catalog_iso2_codes() -> Tuple[str, ...]:
    return tuple(_ISO2_CORE.keys())


def _build_name_index() -> Dict[str, str]:
    index: Dict[str, str] = {}
    for iso2, row in _ISO2_CORE.items():
        iso3, name_en, name_zh, *_ = row
        index[iso2] = iso2
        index[iso3.lower()] = iso2
        index[_normalize_token(name_en)] = iso2
        index[name_zh] = iso2
        compact_en = _normalize_token(name_en).replace(" ", "")
        index[compact_en] = iso2
    for alias, iso2 in _EXTRA_NAME_ALIASES.items():
        index[_normalize_token(alias)] = iso2
        index[alias.replace(" ", "")] = iso2
    return index


_NAME_INDEX = _build_name_index()


def resolve_iso2(value: Any) -> Optional[str]:
    """任意国家输入 → ISO-2。无法识别时返回 None。"""
    if value is None:
        return None
    if isinstance(value, int):
        return None
    token = str(value).strip()
    if not token:
        return None
    lower = token.lower()
    if lower == "uk":
        return "gb"
    if len(lower) == 2 and lower.isascii() and lower.isalpha():
        return lower
    if lower in _NAME_INDEX:
        return _NAME_INDEX[lower]
    iso3 = token.upper()
    if len(iso3) == 3 and iso3.isalpha():
        hit = _NAME_INDEX.get(iso3.lower())
        if hit:
            return hit
    name_key = _normalize_token(token)
    if name_key in _NAME_INDEX:
        return _NAME_INDEX[name_key]
    compact = name_key.replace(" ", "")
    if compact in _NAME_INDEX:
        return _NAME_INDEX[compact]
    return None


def infer_locale(country: Any) -> Dict[str, Any]:
    """为任意国家推断语言 / 时区 / 区号。未知 ISO-2 也会合成自洽参数。"""
    raw = "" if country is None else str(country).strip()
    iso = resolve_iso2(raw) or (raw.lower() if len(raw) == 2 and raw.isalpha() else "")
    if iso == "uk":
        iso = "gb"
    known = lookup_country(iso) if iso else None
    if known:
        result = {
            "lang_code": known["lang_code"],
            "system_lang_code": known["system_lang_code"],
            "tz_offset": int(known["tz_offset"]),
            "dial": known["dial"],
            "alt_system_lang_codes": known["alt_system_lang_codes"],
            "code": known["code"],
            "name": known["name"],
            "name_zh": known["name_zh"],
            "flag": known["flag"],
            "region": known["region"],
            "locale_inferred": False,
        }
        if known.get("tz_offset_range"):
            result["tz_offset_range"] = known["tz_offset_range"]
        return result

    code = iso or (raw.lower() if raw else "")
    lang = "en"
    system = f"en-{code}" if code and len(code) == 2 else "en-us"
    return {
        "lang_code": lang,
        "system_lang_code": system,
        "tz_offset": 0,
        "dial": "",
        "alt_system_lang_codes": (),
        "code": code,
        "name": code.upper() if code else "Unknown",
        "name_zh": "",
        "flag": iso2_flag(code),
        "region": "apac",
        "locale_inferred": True,
    }


def country_display_name(code: Optional[str], default: str = "") -> str:
    meta = lookup_country(code) or infer_locale(code)
    return str(meta.get("name") or default or str(code or "").upper())


def country_display_name_zh(code: Optional[str], default: str = "") -> str:
    meta = lookup_country(code)
    if meta:
        return meta["name_zh"]
    return default


def country_dial_code(code: Optional[str]) -> str:
    meta = lookup_country(code) or infer_locale(code)
    return str(meta.get("dial") or "")


def enrich_country(code: Optional[str], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    locale = infer_locale(code)
    item = {
        "code": locale.get("code") or str(code or "").lower(),
        "name": locale.get("name") or str(code or "").upper(),
        "name_zh": locale.get("name_zh") or "",
        "dial": locale.get("dial") or "",
        "flag": locale.get("flag") or iso2_flag(code),
        "region": locale.get("region") or "apac",
        "lang_code": locale.get("lang_code"),
        "system_lang_code": locale.get("system_lang_code"),
        "tz_offset": locale.get("tz_offset"),
    }
    if extra:
        item.update(extra)
    return item


# SMS-Activate / Grizzly 兼容国家数字 ID（权威表优先在 grizzlysms 内覆盖）。
# 仅作扩展发现：当 getPrices 返回未在权威表登记的 ID 时，仍能映射到 ISO-2。
SMSACTIVATE_ID_TO_ISO2: Dict[int, str] = {
    0: "ru", 1: "ua", 2: "kz", 3: "cn", 4: "ph", 5: "mm", 6: "id", 7: "my",
    8: "ke", 9: "tz", 10: "vn", 11: "kg", 12: "us", 13: "il", 14: "hk",
    15: "pl", 16: "gb", 17: "mg", 18: "cd", 19: "ng", 20: "mo", 21: "eg",
    22: "in", 23: "ie", 24: "kh", 25: "la", 26: "ht", 27: "ci", 28: "gm",
    29: "rs", 30: "ye", 31: "za", 32: "ro", 33: "co", 34: "ee", 35: "az",
    36: "ca", 37: "ma", 38: "gh", 39: "ar", 40: "uz", 41: "cm", 42: "td",
    43: "de", 44: "lt", 45: "hr", 46: "se", 47: "iq", 48: "nl", 49: "lv",
    50: "at", 51: "by", 52: "th", 53: "sa", 54: "mx", 55: "tw", 56: "es",
    57: "ir", 58: "dz", 59: "si", 60: "bd", 61: "sn", 62: "tr", 63: "cz",
    64: "lk", 65: "pe", 66: "pk", 67: "nz", 68: "gn", 69: "ml", 70: "ve",
    71: "et", 72: "mn", 73: "br", 74: "af", 75: "ug", 76: "ao", 77: "cy",
    78: "fr", 79: "pg", 80: "mz", 81: "np", 82: "be", 83: "bg", 84: "hu",
    85: "md", 86: "it", 87: "py", 88: "hn", 89: "tn", 90: "ni", 91: "tl",
    92: "bo", 93: "cr", 94: "gt", 95: "ae", 96: "zw", 97: "pr", 98: "sd",
    99: "tg", 100: "kw", 101: "sv", 102: "ly", 103: "jm", 104: "tt",
    105: "ec", 106: "sz", 107: "om", 108: "ba", 109: "do", 110: "sy",
    111: "qa", 112: "pa", 113: "cu", 114: "mr", 115: "sl", 116: "jo",
    117: "pt", 118: "bb", 119: "bi", 120: "bj", 121: "bn", 122: "bs",
    123: "bw", 124: "bz", 125: "cf", 126: "dm", 127: "gd", 128: "ge",
    131: "gy", 134: "kn", 135: "lr", 136: "ls", 137: "mw", 138: "na",
    139: "ne", 140: "rw", 142: "sr", 144: "mc", 146: "re", 148: "zm",
    149: "so", 151: "cl", 152: "bf", 153: "lb", 154: "ga", 155: "al",
    156: "uy", 157: "mu", 158: "bt", 159: "mv", 160: "gp", 161: "tm",
    162: "gf", 164: "lc", 165: "lu", 166: "vc", 167: "gq", 168: "dj",
    169: "ag", 170: "ky", 171: "me", 172: "dk", 173: "ch", 174: "no",
    175: "au", 176: "er", 177: "ss", 178: "st", 179: "aw", 182: "jp",
    183: "mk", 184: "sc", 185: "nc", 186: "cv", 187: "us", 188: "ps",
    189: "fj", 190: "kr",
    # 与现有权威反向表保持一致的特殊项（覆盖 SMS-Activate 默认）
    129: "ge", 130: "gr", 133: "is", 143: "sk", 145: "tj", 147: "bh", 150: "am",
    163: "fi",
    # 官方 SMS-Activate 别名（与现有权威 ID 并存，仅用于反向展示）
    132: "is", 141: "sk",
}


def smsactivate_id_to_iso2(country_id: Any) -> Optional[str]:
    try:
        cid = int(country_id)
    except (TypeError, ValueError):
        return None
    return SMSACTIVATE_ID_TO_ISO2.get(cid)


def iso2_to_smsactivate_id(code: Optional[str]) -> Optional[int]:
    iso = resolve_iso2(code)
    if not iso:
        return None
    for cid, mapped in SMSACTIVATE_ID_TO_ISO2.items():
        if mapped == iso:
            # 美国优先 187，12 是虚拟号别名
            if iso == "us" and cid == 12:
                continue
            return cid
    return None
