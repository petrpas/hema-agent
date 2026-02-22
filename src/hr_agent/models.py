from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, RootModel


class HemaWeapon(StrEnum):
    LS = "LS"   # Long Sword
    SA = "SA"   # Sabre
    RA = "RA"   # Rapier
    RD = "RD"   # Rapier and Dagger
    SB = "SB"   # Sword and Buckler

class HemaGender(StrEnum):
    Man = "M"
    Woman = "W"
    Open = "O"
    OpenByDefault = ""

class HemaMaterial(StrEnum):
    Steel = "Steel"
    Plastic = "Plastic"
    SteelByDefault = ""

class HemaDiscipline(BaseModel):
    weapon: HemaWeapon
    gender: HemaGender | None = HemaGender.OpenByDefault
    material: HemaMaterial | None = HemaMaterial.SteelByDefault

    def str(self):
        mat = self.material if self.material and self.material != HemaMaterial.Steel else ""
        return (mat + " " if mat else "") + self.weapon + (self.gender if self.gender == HemaGender.Woman else "")


class FencerRating(BaseModel):
    hr_id: int
    weapon: str       # LS / SA / RA / SB / RD
    rating: float | None
    rank: int | None

class FencerRecord(BaseModel):
    registration_time: str = Field(description="Use isoformat (e.g. 2020-03-14T15:32:52.00).")
    name: str = Field(description="Full name of the fencer e.g. John Smith. Always put first name first.")
    nationality: str = Field(description="Fencer's nationality abbr. e.g. CZ, SK, DE, US, etc., if explicitly mentioned", default="")
    email: str | None
    club: str | None
    hr_id: int | None = Field(description="int or none if not present or anything but int set")  # None = unknown
    disciplines: list[HemaDiscipline] = Field(description="if not set explicitly otherwise assume gender=OpenByDefault and material=SteelByDefault")
    borrow: list[HemaWeapon] = []
    after_party: Literal["Yes"] | Literal["No"] | Literal["Oth"] | None = Field(description="Select closest possible option, if Oth, put a note into notes.")
    notes: str | None = Field(description="Anything that fencer put into registration form but did not got into other fields")
    problems: str | None = Field(description="If the content does not match perfectly, list problems here")
