from pathlib import Path
from typing import Callable, Sequence
from fractions import Fraction
from vstools import (
    vs,
    core,
    initialize_clip,
    KwargsT,
    ColorRangeT,
    MatrixT,
    TransferT,
    PrimariesT,
    DitherType,
    FieldBasedT,
    ChromaLocationT,
)
from muxtools import (
    Trim,
    PathLike,
    parse_m2ts_path,
    ensure_path_exists,
    info,
    get_workdir,
    get_temp_workdir,
    clean_temp_files,
    get_absolute_track,
    TrackType,
    GlobSearch,
    error,
    sanitize_trims,
    SourceFilter
)


__all__ = ["src_file", "SRC_FILE", "FileInfo", "src", "frames_to_samples", "f2s"]


class src_file:
    file: Path | list[Path]
    source_filter: SourceFilter | Callable[[str], vs.VideoNode] = SourceFilter.BESTSOURCE
    preview_filter: SourceFilter | Callable[[str], vs.VideoNode] = SourceFilter.FFMS2
    trim: Trim = None

    def __init__(
        self,
        file: PathLike | GlobSearch | Sequence[PathLike],
        source_filter: SourceFilter | Callable[[str], vs.VideoNode] = SourceFilter.BESTSOURCE,
        preview_filter: SourceFilter | Callable[[str], vs.VideoNode] = SourceFilter.FFMS2,
        trim: Trim = None,
    ):
        """
        Custom `FileInfo` kind of thing for convenience

        :param file:            Either a string based filepath or a Path object
        :param source_filter:   Source filter or callable to use for regular indexing
        :param preview_filter:  Source filter or callable to use when in preview mode
        :param trim:            Can be a single trim or a sequence of trims
        """
        if isinstance(file, Sequence) and not isinstance(file, str) and len(file) == 1:
            file = file[0]

        self.file = (
            [ensure_path_exists(f, self) for f in file]
            if isinstance(file, Sequence) and not isinstance(file, str)
            else ensure_path_exists(file, self)
        )
        self.source_filter = source_filter
        self.preview_filter = preview_filter
        self.trim = trim

    def __call_indexer(self, fileIn: Path) -> vs.VideoNode:
        if callable(self.source_filter):
            return self.source_filter(str(fileIn.resolve()))
        else:
            return src(fileIn, self.source_filter, self.preview_filter)

    def __index_clip(self):
        if isinstance(self.file, list):
            indexed = core.std.Splice([self.__call_indexer(f) for f in self.file])
        else:
            indexed = self.__call_indexer(self.file)
        cut = indexed
        if self.trim:
            self.trim = list(self.trim)
            if self.trim[0] is None:
                self.trim[0] = 0
            if self.trim[1] is None or self.trim[1] == 0:
                if self.trim[0] < 0:
                    cut = (indexed[0] * abs(self.trim[0])) + indexed
                else:
                    cut = indexed[self.trim[0] :]
            else:
                if self.trim[0] < 0:
                    cut = (indexed[0] * abs(self.trim[0])) + indexed[: self.trim[1]]
                else:
                    cut = indexed[self.trim[0] : self.trim[1]]
            self.trim = tuple(self.trim)

        if not isinstance(self.file, list) and self.file.suffix.lower() == ".dgi":
            if self.file.with_suffix(".m2ts").exists():
                self.file = self.file.with_suffix(".m2ts")
            else:
                self.file = parse_m2ts_path(self.file)

        setattr(self, "clip", indexed)
        setattr(self, "clip_cut", cut)

    @property
    def src(self) -> vs.VideoNode:
        if not hasattr(self, "clip"):
            self.__index_clip()
        return self.clip

    @property
    def src_cut(self) -> vs.VideoNode:
        if not hasattr(self, "clip_cut"):
            self.__index_clip()
        return self.clip_cut

    def init(
        self,
        bits: int | None = None,
        matrix: MatrixT | None = None,
        transfer: TransferT | None = None,
        primaries: PrimariesT | None = None,
        chroma_location: ChromaLocationT | None = None,
        color_range: ColorRangeT | None = None,
        field_based: FieldBasedT | None = None,
        strict: bool = False,
        dither_type: DitherType = DitherType.AUTO,
    ) -> vs.VideoNode:
        """
        Getter that calls `vstools.initialize_clip` on the src clip for convenience
        """
        return initialize_clip(
            self.src, bits, matrix, transfer, primaries, chroma_location, color_range, field_based, strict, dither_type, func=self.init
        )

    def init_cut(
        self,
        bits: int | None = None,
        matrix: MatrixT | None = None,
        transfer: TransferT | None = None,
        primaries: PrimariesT | None = None,
        chroma_location: ChromaLocationT | None = None,
        color_range: ColorRangeT | None = None,
        field_based: FieldBasedT | None = None,
        strict: bool = False,
        dither_type: DitherType = DitherType.AUTO,
    ) -> vs.VideoNode:
        """
        Getter that calls `vstools.initialize_clip` on the src_cut clip for convenience
        """
        return initialize_clip(
            self.src_cut, bits, matrix, transfer, primaries, chroma_location, color_range, field_based, strict, dither_type, func=self.init_cut
        )

    def get_audio(self, track: int = 0, **kwargs) -> vs.AudioNode:
        """
        Indexes the specified audio track from the input file(s).
        """
        file = self.file if isinstance(self.file, list) else [self.file]

        nodes = list[vs.AudioNode]()
        for f in file:
            absolute = get_absolute_track(f, track, TrackType.AUDIO)
            nodes.append(core.bs.AudioSource(str(f.resolve()), absolute, **kwargs))

        return nodes[0] if len(nodes) == 1 else core.std.AudioSplice(nodes)

    def get_audio_trimmed(self, track: int = 0, **kwargs) -> vs.AudioNode:
        """
        Gets the indexed audio track with the trim specified in the src_file.
        """
        node = self.get_audio(track, **kwargs)
        if self.trim:
            if self.trim[1] is None or self.trim[1] == 0:
                node = node[f2s(self.trim[0], node, self.src) :]
            else:
                node = node[f2s(self.trim[0], node, self.src) : f2s(self.trim[1], node, self.src)]
        return node

    @staticmethod
    def BDMV(
        root_dir: PathLike,
        playlist: int = 0,
        entries: int | list[int] | Trim | None = None,
        angle: int = 0,
        trim: Trim | None = None,
        source_filter: SourceFilter | Callable[[str], vs.VideoNode] = SourceFilter.BESTSOURCE,
        preview_filter: SourceFilter | Callable[[str], vs.VideoNode] = SourceFilter.LSMASH,
        **kwargs: KwargsT,
    ) -> "src_file":
        root_dir = ensure_path_exists(root_dir, "BDMV", True)
        mpls = core.mpls.Read(str(root_dir), playlist, angle)
        clips: list[str] = mpls["clip"]
        if entries is not None:
            if isinstance(entries, int):
                clips = clips[entries]
            elif isinstance(entries, list):
                entries = sanitize_trims(entries)
            else:
                if entries[0] is None and entries[1]:
                    clips = clips[: entries[1]]
                elif entries[1] is None:
                    clips = clips[entries[0] :]
                else:
                    clips = clips[entries[0] : entries[1]]
        return src_file(clips, source_filter, preview_filter, trim)


SRC_FILE = src_file
FileInfo = src_file


def src(
    filePath: PathLike,
    source_filter: SourceFilter = SourceFilter.BESTSOURCE,
    preview_filter: SourceFilter = SourceFilter.FFMS2,
    **kwargs: KwargsT
) -> vs.VideoNode:
    """
    Indexes video files using various source filters.

    :param filepath:        Path to video or dgi file
    :param source_filter:   Source filter to use for regular indexing
    :param preview_filter:  Source filter to use when previewing
    :param kwargs:          Additional arguments to pass to the indexer
    :return:                Video Node
    
    """
    filePath = ensure_path_exists(filePath, src)
    dgiFile = filePath.with_suffix(".dgi")

    if filePath.suffix.lower() == ".dgi" or dgiFile.exists():
        if not hasattr(core, "dgdecodenv"):
            raise error("Trying to use a dgi file without dgdecodenv installed.", src)
        return core.lazy.dgdecodenv.DGSource(
            str(filePath.resolve()) if not dgiFile.exists() else str(dgiFile.resolve()),
            **kwargs
        ) # type: ignore

    is_previewing = False
    try:
        from vspreview.api import is_preview
        is_previewing = is_preview()
    except:
        pass

    selected_filter = preview_filter if is_previewing else source_filter
    
    if selected_filter == SourceFilter.BESTSOURCE:
        if not hasattr(core, "bs"):
            raise error("Bestsource requested but not installed!", src)
        show_progress = kwargs.pop("showprogress", True)
        return core.lazy.bs.VideoSource(str(filePath.resolve()), showprogress=show_progress, **kwargs)
    
    elif selected_filter == SourceFilter.LSMASH:
        if not hasattr(core, "lsmas"):
            raise error("LSMASH requested but not installed!", src)
        return core.lazy.lsmas.LWLibavSource(str(filePath.resolve()), **kwargs)
    
    elif selected_filter == SourceFilter.FFMS2:
        if not hasattr(core, "ffms2"):
            raise error("FFMS2 requested but not installed!", src)
        return core.lazy.ffms2.Source(str(filePath.resolve()), **kwargs)
    
    elif selected_filter == SourceFilter.DGDECNV:
        if not hasattr(core, "dgdecodenv"):
            raise error("DGDecodeNV requested but not installed!", src)
        return core.lazy.dgdecodenv.DGSource(str(filePath.resolve()), **kwargs)

    else:
        raise error("No valid source filter found", src)


def frames_to_samples(frame: int, sample_rate: vs.AudioNode | int = 48000, fps: vs.VideoNode | Fraction = Fraction(24000, 1001)) -> int:
    """
    Converts a frame number to a sample number

    :param frame:           The frame number
    :param sample_rate:     Can be a flat number like 48000 (=48 kHz) or an AudioNode to get the sample rate from
    :param fps:             Can be a Fraction or a VideoNode to get the fps from

    :return:                The sample number
    """
    if frame == 0:
        return 0
    sample_rate = sample_rate.sample_rate if isinstance(sample_rate, vs.AudioNode) else sample_rate
    fps = Fraction(fps.fps_num, fps.fps_den) if isinstance(fps, vs.VideoNode) else fps
    return int(sample_rate * (fps.denominator / fps.numerator) * frame)


f2s = frames_to_samples


def generate_keyframes(clip: vs.VideoNode, start_frame: int = 0) -> list[int]:
    clip = clip.resize.Bilinear(640, 360, format=vs.YUV410P8)
    clip = clip.wwxd.WWXD()
    if start_frame:
        clip = clip[start_frame:]

    frames = list[int]()
    for i in range(1, clip.num_frames):
        if clip.get_frame(i).props.Scenechange == 1:
            frames.append(i)

    return frames


def generate_qp_file(clip: vs.VideoNode, start_frame: int = 0) -> str:
    filepath = Path(get_workdir(), f"qpfile_{start_frame}.txt")
    temp = Path(get_temp_workdir(), "qpfile.txt")
    if filepath.exists():
        info("Reusing existing QP File.")
        return str(filepath.resolve())
    info("Generating QP File...")

    out = ""
    keyframes = generate_keyframes(clip, start_frame)

    for i in keyframes:
        out += f"{i} I -1\n"

    with open(temp, "w") as file:
        file.write(out)

    temp.rename(filepath)
    clean_temp_files()

    return str(filepath.resolve())
