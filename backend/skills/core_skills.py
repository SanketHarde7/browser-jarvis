from datetime import datetime
from skills.base_skill import BaseSkill

class TimeSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "time_now"
        
    @property
    def description(self) -> str:
        return "Tells the current time."

    def execute(self, *args, **kwargs) -> str:
        now = datetime.now()
        time_fmt = now.strftime("%I:%M %p").lstrip('0')
        day_fmt = now.strftime("%A")
        return f"It is {time_fmt} on {day_fmt}."

class DateSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "date_today"
        
    @property
    def description(self) -> str:
        return "Tells the current date."

    def execute(self, *args, **kwargs) -> str:
        today = datetime.now()
        date_fmt = today.strftime("%A, %B %d, %Y")
        return f"Today is {date_fmt}."

class SysInfoSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "sysinfo"
        
    @property
    def description(self) -> str:
        return "Retrieves system information like CPU and RAM usage."

    def execute(self, detail: str = "all", *args, **kwargs) -> str:
        from modules.sysinfo import get_system_info
        return get_system_info(detail)

class TopProcessesSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "top_processes"
        
    @property
    def description(self) -> str:
        return "Gets the top resource-consuming processes."

    def execute(self, n: str = "5", *args, **kwargs) -> str:
        from modules.sysinfo import get_top_processes
        return get_top_processes(int(n) if n.isdigit() else 5)
