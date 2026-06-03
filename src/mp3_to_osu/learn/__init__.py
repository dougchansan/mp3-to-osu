"""Style-learning subsystem (analyzer).

Parse a corpus of real .osu beatmaps, extract how they are built (rhythm,
object-type flow, spacing/jump velocity, angles, combo cadence, sliders), and
aggregate per-mapper StyleProfiles that the generator can later sample from.
"""
