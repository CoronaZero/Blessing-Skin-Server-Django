"""players 模型测试（TDD 先行）。

对照 PHP 原版 Player：
- pid/uid/name/tid_skin/tid_cape/last_modified 字段语义
- uuid 独立表并入本模型（uuid 列 + 查询辅助，值与原版 v3/v4 算法产物一致）
- CustomSkinAPI R1 JSON：{username, skins: {default|slim: hash}, cape: hash}
- model 派生属性：skin.type == 'alex' → 'slim'，否则 'default'
"""

import pytest

from accounts.models import User
from players.models import Player
from players.uuidgen import generate_uuid_v3, generate_uuid_v4

# 原版 ygg_uuid_algorithm=v3 时的 UUID 生成算法：
# md5('OfflinePlayer:' + name) 后按 RFC 4122 v3 语义修正 version/variant 位
NAME = "Steve"
V3_UUID = generate_uuid_v3(NAME)


class TestPlayerModel:
    def test_create_player_with_relations(self, db):
        user = User.objects.create_user(email="p@x.com", nickname="p", password="x")
        player = Player.objects.create(uid=user, name="Steve")
        assert player.pid is not None
        assert player.name == "Steve"
        # 无皮肤/披风：外键为 NULL（原版 tid_* = 0 的语义等价物）
        assert player.tid_skin is None
        assert player.tid_cape is None
        assert player.last_modified is not None

    def test_player_belongs_to_user(self, db):
        user = User.objects.create_user(email="r@x.com", nickname="r", password="x")
        player = Player.objects.create(uid=user, name="Alex")
        assert player.uid == user
        assert player.uid.email == "r@x.com"
        assert user.players.count() == 1
        assert user.players.first().name == "Alex"

    def test_skin_cape_relations(self, db):
        from textures.models import Texture

        user = User.objects.create_user(email="t@x.com", nickname="t", password="x")
        skin = Texture.objects.create(
            name="skin", type="steve", hash="a" * 64, size=1, uploader=user, public=True
        )
        cape = Texture.objects.create(
            name="cape", type="cape", hash="b" * 64, size=1, uploader=user, public=True
        )
        player = Player.objects.create(uid=user, name="Notch", tid_skin=skin, tid_cape=cape)
        assert player.tid_skin.name == "skin"
        assert player.tid_cape.name == "cape"
        assert player.model == "default"

    def test_model_property_slim_for_alex(self, db):
        from textures.models import Texture

        user = User.objects.create_user(email="m@x.com", nickname="m", password="x")
        alex = Texture.objects.create(
            name="alex-skin", type="alex", hash="c" * 64, size=1, uploader=user, public=True
        )
        player = Player.objects.create(uid=user, name="Alex", tid_skin=alex)
        assert player.model == "slim"

    def test_model_property_default_without_skin(self, db):
        user = User.objects.create_user(email="d@x.com", nickname="d", password="x")
        player = Player.objects.create(uid=user, name="NoSkin")
        assert player.model == "default"

    def test_unique_name(self, db):
        """角色名全局唯一（对照原版注册查重逻辑）。"""
        user = User.objects.create_user(email="u@x.com", nickname="u", password="x")
        Player.objects.create(uid=user, name="Steve")
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            Player.objects.create(uid=user, name="Steve")

    def test_name_length_physical_limit(self, db):
        """模型层仅限物理上限（对照原版 varchar(50)）；
        业务层规则（official/cjk/utf8/custom）在 Phase 2 按 option 实现。
        """
        user = User.objects.create_user(email="l@x.com", nickname="l", password="x")
        # 长度在 50 内的任意名字均可在模型层保存（utf8 模式允许 CJK）
        Player.objects.create(uid=user, name="中文名字")
        from django.core.exceptions import ValidationError
        from django.db import DataError

        with pytest.raises((ValidationError, DataError)):
            p = Player(uid=user, name="a" * 51)
            p.full_clean()


class TestCustomSkinApiJson:
    """CustomSkinAPI R1 输出契约（/{player}.json 静态端点在 Phase 3 对接）。"""

    def test_to_profile_dict_with_skin_and_cape(self, db):
        from textures.models import Texture

        user = User.objects.create_user(email="j@x.com", nickname="j", password="x")
        skin = Texture.objects.create(
            name="s", type="steve", hash="aa" * 32, size=1, uploader=user, public=True
        )
        cape = Texture.objects.create(
            name="c", type="cape", hash="bb" * 32, size=1, uploader=user, public=True
        )
        player = Player.objects.create(
            uid=user, name="Steve", tid_skin=skin, tid_cape=cape
        )
        data = player.to_profile_dict()
        assert data == {
            "username": "Steve",
            "skins": {"default": "aa" * 32},
            "cape": "bb" * 32,
        }

    def test_to_profile_dict_slim_model(self, db):
        from textures.models import Texture

        user = User.objects.create_user(email="k@x.com", nickname="k", password="x")
        skin = Texture.objects.create(
            name="s", type="alex", hash="cc" * 32, size=1, uploader=user, public=True
        )
        player = Player.objects.create(uid=user, name="Alex", tid_skin=skin)
        assert player.to_profile_dict() == {
            "username": "Alex",
            "skins": {"slim": "cc" * 32},
            "cape": None,
        }


class TestUuidGeneration:
    """对照原版 ygg uuid 表的两种算法。"""

    def test_v3_offline_player_algorithm(self):
        """v3：md5('OfflinePlayer:'+name) + version/variant 位修正。

        参考向量（原版 generateUuidV3 注释中的 games647 gist）：
        'games647' → 按 OfflinePlayer 语义可复现。
        """
        uuid = generate_uuid_v3("Steve")
        assert len(uuid) == 32
        assert all(c in "0123456789abcdef" for c in uuid)
        # version 位 = 3（第 13 个 hex 字符高 4 位为 0011）
        third_group = int(uuid[12:16], 16)
        assert (third_group >> 12) == 3
        # variant 位 = 10xx（第 17 个 hex 字符高 2 位）
        fourth_group = int(uuid[16:20], 16)
        assert (fourth_group >> 14) == 2

    def test_v3_deterministic(self):
        assert generate_uuid_v3("Steve") == generate_uuid_v3("Steve")
        assert generate_uuid_v3("Steve") != generate_uuid_v3("Alex")

    def test_v4_format(self):
        uuid = generate_uuid_v4()
        assert len(uuid) == 32
        assert all(c in "0123456789abcdef" for c in uuid)
        third_group = int(uuid[12:16], 16)
        assert (third_group >> 12) == 4
        fourth_group = int(uuid[16:20], 16)
        assert (fourth_group >> 14) == 2

    def test_v4_randomness(self):
        assert generate_uuid_v4() != generate_uuid_v4()


class TestPlayerUuidColumn:
    """uuid 内嵌到 Player（v2 计划：吸收原独立 uuid 表）。"""

    def test_uuid_auto_assigned_on_create(self, db):
        """创建角色自动分配 v4（默认算法，对照原版 ygg_uuid_algorithm 默认 v4）。"""
        user = User.objects.create_user(email="g@x.com", nickname="g", password="x")
        player = Player.objects.create(uid=user, name="Notch")
        assert len(player.uuid) == 32

    def test_uuid_stable_after_rename(self, db):
        """改名不改 UUID（对照原版 updating 回调保证的一致性约束）。"""
        user = User.objects.create_user(email="h@x.com", nickname="h", password="x")
        player = Player.objects.create(uid=user, name="Dinnerbone")
        original = player.uuid
        player.name = "Dinnerbone2"
        player.save()
        player.refresh_from_db()
        assert player.uuid == original

    def test_uuid_unique(self, db):
        from django.db import IntegrityError

        user = User.objects.create_user(email="i@x.com", nickname="i", password="x")
        Player.objects.create(uid=user, name="A", uuid="f" * 32)
        with pytest.raises(IntegrityError):
            Player.objects.create(uid=user, name="B", uuid="f" * 32)

    def test_find_by_uuid(self, db):
        user = User.objects.create_user(email="q@x.com", nickname="q", password="x")
        Player.objects.create(uid=user, name="Honey", uuid=V3_UUID)
        assert Player.objects.filter(uuid=V3_UUID).exists()
