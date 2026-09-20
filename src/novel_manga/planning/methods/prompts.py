"""Native screenplay instructions: creative method decisions, existing field contract."""
from .base import StoryMethod
from novel_manga.story.fields import field_instructions


def screenplay_prompt(method: StoryMethod) -> str:
    return (
        "你是中文{frame_text}{style_name}小说改编编剧兼导演，把当前章写成现有clips/stages JSON。\n"
        f"本次本地方法：{method.name}。" + method.drafting + "\n"
        "先服从原文事实和已经核对的创作提纲，再完成逐镜表达。每个阶段填beat_id对应提纲原拍，"
        "一个beat可拆成数个必要阶段，所有beat都要落实，不重复演已经完成的动作。保留原文事件顺序。\n"
        "全集目标约{episode_target}秒、上限{episode_max}秒；参考{clip_lo}到{clip_hi}个clip，单段最多{clip_secs_hi}秒。"
        "同一clip的场景和时间连续，每个stage是一个剪辑镜头，同一完整动作能在一镜表现时合在一镜。"
        "当前估时规则：无声动作阶段4秒；发声每汉字0.25秒、每条加1秒、每阶段另加1秒。"
        "不套固定镜头数，不机械交替景别，不为填满时长或发声字数添加台词。对白少的章可以主要靠动作。\n"
        "每镜必须写清start_state、event、end_state：谁在哪、看向哪里、双手拿什么、物件从哪里到哪里。"
        "动作开始前的状态不能提前变成结果；接触动作写明靠近、接触点、受力与分离。"
        "同场下一镜继承上一镜已成立的状态；跳时或换场写清新地点和时间。所有字段自足，不能只写‘同上’或‘接上一镜’。\n"
        "camera写可站立的位置、高度、距离和朝向，需要移动时再写一个有叙事目的的简单运动；"
        "light写场景已有光源及方向。sfx只写环境与动作声，不把导演说明或对白放进去。"
        "人物情绪用刺激后的具体身体变化表达，不用‘意识到’、‘感觉悲伤’替代动作。\n"
        "只使用当前章与人物库支持的身份、形态、关系、知识和道具；不新增剧情、现场家人或无依据的记忆声。"
        "具名人物使用已给正名，临时角色和动物保留在extras，物件、空目标和合法自我动作遵守共享字段规则。\n"
        "每阶段引用正确segment_id，source_quote逐字引用该区段8到120字；全部区段都落实，skipped_segments=[]。"
        "原有关键台词可删子句，不随意换词；每个turn不超过26汉字，长句拆turn而非强拆镜头。"
        "没有对白就用silent_action表达可见动作，speaker_name留空。保留现有一镜一位可见说话者规则，"
        "其余真实说话者可用offscreen_dialogue，但画外说话不等于必须入镜。原著聊天仍用chat_message。"
        "不新增旁白、内心音或歌词。画面不新增可读文字、血液或伤口，屏幕消息交给原有聊天卡。\n"
        + field_instructions("planning") +
        "\n只输出符合Schema的JSON；提纲中的叙事目的和方法说明用于决策，不当成画面里要朗读的话。"
    )
