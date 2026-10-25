"""Versioned, local update protocol. Validation never executes a package."""
from dataclasses import dataclass
import hashlib,json,re
from pathlib import Path
from typing import Protocol

PRODUCT_ID='POLY5-748D8316-6D5E-42CF-90DA-4F84E77A6F20'
MANIFEST_SCHEMA=1
SETTINGS_SCHEMA=1
EVENT_SCHEMA='3.1'

def version_key(value):
    match=re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:-dev\.(\d+))?',str(value))
    if not match:raise ValueError('无法识别版本号')
    major,minor,patch,dev=match.groups()
    return int(major),int(minor),int(patch),1 if dev is None else 0,int(dev or 0)

class UpdateSource(Protocol):
    """Future trusted channels implement fetch_manifest; never change trading state."""
    def fetch_manifest(self)->tuple[dict,Path]:...

@dataclass
class LocalUpdateSource:
    manifest_path: Path
    def fetch_manifest(self):
        path=Path(self.manifest_path).resolve(strict=True)
        if path.stat().st_size>64*1024:raise ValueError('更新清单过大')
        return json.loads(path.read_text(encoding='utf-8-sig')),path.parent

def check_update(source:UpdateSource,current_version):
    manifest,folder=source.fetch_manifest()
    if manifest.get('schema_version')!=MANIFEST_SCHEMA or manifest.get('product_id')!=PRODUCT_ID:
        raise ValueError('更新清单格式或产品身份不匹配')
    if manifest.get('platform')!='windows-x64' or manifest.get('channel')!='development':
        raise ValueError('更新平台或通道不匹配')
    target=manifest.get('version');key=version_key(target);current=version_key(current_version)
    if key<current:raise ValueError('不允许降级覆盖当前程序')
    compatible=manifest.get('compatibility',{})
    minimum=compatible.get('settings_schema_min');maximum=compatible.get('settings_schema_max')
    if type(minimum) is not int or type(maximum) is not int or not minimum<=SETTINGS_SCHEMA<=maximum:
        raise ValueError('更新不兼容当前设置格式')
    if EVENT_SCHEMA not in compatible.get('readable_event_schemas',[]):
        raise ValueError('更新不兼容现有事实账本格式')
    package=manifest.get('package',{});name=package.get('file','')
    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+\.exe',name):
        raise ValueError('更新安装包路径非法')
    path=(folder/name).resolve(strict=True)
    if path.parent!=folder.resolve():raise ValueError('安装包不能指向清单目录之外')
    expected=package.get('sha256','');size=package.get('bytes')
    if not re.fullmatch('[a-f0-9]{64}',str(expected)) or type(size) is not int or size<=0:
        raise ValueError('安装包校验字段非法')
    if path.stat().st_size!=size:raise ValueError('安装包大小校验失败')
    with path.open('rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
    if actual!=expected:raise ValueError('安装包 SHA-256 校验失败')
    return {'status':'update_available' if key>current else 'current','current_version':current_version,
            'version':target,'package':str(path),'sha256':actual,'bytes':size,
            'notes':str(manifest.get('notes',''))[:2000],
            'trust':'local_user_selected_package; checksum proves integrity, not publisher identity',
            'restart_required':True,'auto_install':False}
