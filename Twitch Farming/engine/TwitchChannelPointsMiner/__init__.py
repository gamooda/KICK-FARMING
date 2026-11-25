# -*- coding: utf-8 -*-
__version__ = "2.0.7"

__all__ = ["TwitchChannelPointsMiner"]


def __getattr__(name):
    if name == "TwitchChannelPointsMiner":
        from .TwitchChannelPointsMiner import TwitchChannelPointsMiner
        return TwitchChannelPointsMiner
    raise AttributeError("module 'TwitchChannelPointsMiner' has no attribute " + repr(name))
