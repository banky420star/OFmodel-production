"""
Persona Studio — Artistic Style Library

100+ photography and video styles for content production.
Each style includes: image prompt, video prompt, lighting, mood, and category.

Categories:
  - fine_art: Classical and contemporary fine art photography
  - boudoir: Intimate boudoir and glamour photography
  - editorial: Fashion and editorial photography
  - portrait: Portrait and beauty photography
  - conceptual: Abstract and conceptual art
  - cinematic: Film and cinema-inspired
  - naturista: Natural/nature-connected
  - video: Video-specific motion styles
"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class ArtisticStyle:
    name: str
    category: str
    image_prompt: str
    video_prompt: str
    lighting: str
    mood: str
    tags: list[str] = field(default_factory=list)


# ─── FINE ART ──────────────────────────────────────────────────────

FINE_ART_STYLES = [
    ArtisticStyle(
        name="Renaissance Oil Painting",
        category="fine_art",
        image_prompt="painted in the style of a Renaissance oil masterpiece, rich chiaroscuro lighting, warm golden tones, soft sfumato skin rendering, classical composition with draped fabrics",
        video_prompt="slow subtle movement as if the painting is coming alive, gentle breathing, fabric shifting softly, Renaissance masterwork animation",
        lighting="Rembrandt lighting, warm candlelight glow",
        mood="timeless, regal, contemplative",
    ),
    ArtisticStyle(
        name="Baroque Drama",
        category="fine_art",
        image_prompt="Baroque-style dramatic portrait, deep shadows and bright highlights, Caravaggio-inspired tenebrism, rich velvet textures, theatrical pose",
        video_prompt="dramatic reveal as light slowly illuminates the subject from darkness, Baroque chiaroscuro animation",
        lighting="single source tenebrism, deep blacks",
        mood="dramatic, intense, powerful",
    ),
    ArtisticStyle(
        name="Impressionist Soft Focus",
        category="fine_art",
        image_prompt="Impressionist-style soft dreamy portrait, dappled light through leaves, Monet-inspired color palette, painterly brushstroke texture, pastel tones",
        video_prompt="gentle sway like a Monet painting, dappled light shifting, wind through hair, Impressionist dreamlike motion",
        lighting="dappled natural light, soft diffusion",
        mood="dreamy, romantic, ethereal",
    ),
    ArtisticStyle(
        name="Art Nouveau",
        category="fine_art",
        image_prompt="Art Nouveau style portrait, flowing organic lines, Mucha-inspired composition, ornate floral borders, sinuous curves, muted jewel tones",
        video_prompt="flowing organic lines animate and swirl around the subject, Art Nouveau ornamental motion",
        lighting="soft diffused studio light, warm undertones",
        mood="elegant, ornamental, flowing",
    ),
    ArtisticStyle(
        name="Pop Art",
        category="fine_art",
        image_prompt="Andy Warhol pop art style, bold flat colors, halftone dot pattern, high contrast, screen print aesthetic, bright neon palette",
        video_prompt="color shifts and halftone animations cycling through bold Warhol color palettes, pop art motion graphics",
        lighting="flat even studio lighting, no shadows",
        mood="bold, playful, iconic",
    ),
    ArtisticStyle(
        name="Japanese Ukiyo-e",
        category="fine_art",
        image_prompt="Ukiyo-e woodblock print style, flat color planes, bold outlines, traditional Japanese composition, mist and nature elements, muted indigo and vermillion",
        video_prompt="gentle parallax motion like a living woodblock print, waves and mist flowing, Ukiyo-e animation",
        lighting="flat even light, no shadows, graphic",
        mood="serene, meditative, traditional",
    ),
    ArtisticStyle(
        name="Cubist Fragmented",
        category="fine_art",
        image_prompt="Cubist-inspired fragmented portrait, multiple angles shown simultaneously, geometric planes, Picasso-style deconstruction, muted earth tones",
        video_prompt="geometric planes shifting and rotating around the subject, Cubist animation of form",
        lighting="flat even light across all planes",
        mood="avant-garde, intellectual, abstract",
    ),
    ArtisticStyle(
        name="Surrealist Dream",
        category="fine_art",
        image_prompt="Salvador Dalí surrealist style, melting elements, impossible physics, dreamlike landscape, hyperrealistic rendering of surreal concepts",
        video_prompt="slow surreal transformations, melting clocks, floating objects, Dalí-inspired impossible motion",
        lighting="golden hour surreal glow, long shadows",
        mood="dreamlike, bizarre, fantastical",
    ),
    ArtisticStyle(
        name="Minimalist Negative Space",
        category="fine_art",
        image_prompt="extreme minimalist composition, vast negative space, subject small in frame, clean lines, monochromatic, museum-quality fine art print",
        video_prompt="slow zoom into the subject from vast negative space, minimalist reveal, meditative pace",
        lighting="soft even light, no harsh shadows",
        mood="contemplative, serene, modern",
    ),
    ArtisticStyle(
        name="Classical Greek Sculpture",
        category="fine_art",
        image_prompt="styled as a classical Greek marble sculpture, white marble texture, dramatic museum lighting, contrapposto pose, draped fabric in stone",
        video_prompt="subtle life emerging from marble, slow blink, gentle breath, statue coming to life",
        lighting="museum spot lighting, dramatic shadows on marble",
        mood="timeless, statuesque, divine",
    ),
    ArtisticStyle(
        name="Chiaroscuro Master",
        category="fine_art",
        image_prompt="extreme chiaroscuro portrait, half the face in deep shadow, half illuminated by warm light, Caravaggio or Rembrandt style, fine art museum quality",
        video_prompt="light slowly crossing the face, shadow and light dancing, chiaroscuro transition",
        lighting="single dramatic side light, deep shadows",
        mood="mysterious, intense, classical",
    ),
    ArtisticStyle(
        name="Watercolor Portrait",
        category="fine_art",
        image_prompt="watercolor painting style, bleeding colors, wet-on-wet technique, soft edges dissolving into white paper, transparent layers, delicate and fluid",
        video_prompt="colors bleeding and flowing like watercolor on wet paper, paint dripping and spreading, fluid art motion",
        lighting="soft diffused natural light",
        mood="delicate, fluid, artistic",
    ),
    ArtisticStyle(
        name="Charcoal Sketch",
        category="fine_art",
        image_prompt="charcoal drawing on textured paper, expressive gestural lines, dramatic smudging, high contrast black and white, raw and emotional",
        video_prompt="charcoal lines being drawn in real-time, sketch coming to life, artistic creation animation",
        lighting="high contrast studio, dramatic single source",
        mood="raw, expressive, emotional",
    ),
    ArtisticStyle(
        name="Vermeer Light",
        category="fine_art",
        image_prompt="Vermeer-inspired portrait, window light falling on subject, intimate domestic scene, pearl-like skin rendering, Dutch Golden Age masterwork quality",
        video_prompt="gentle domestic scene, light shifting through window, quiet intimate moment, Vermeer painting animation",
        lighting="single window light, soft directional glow",
        mood="intimate, quiet, luminous",
    ),
    ArtisticStyle(
        name="Gustav Klimt Gold",
        category="fine_art",
        image_prompt="Gustav Klimt inspired portrait, gold leaf textures, intricate geometric patterns, mosaic-like composition, The Kiss aesthetic, ornamental luxury",
        video_prompt="gold leaf patterns shimmering and shifting, Klimt-inspired ornamental animation, glittering mosaic motion",
        lighting="warm golden ambient light",
        mood="luxurious, ornamental, sensual",
    ),
    # ─── EXPANDED FINE ART NUDE STYLES ──────────────────────────────
    ArtisticStyle(
        name="Classical Figure Study",
        category="fine_art",
        image_prompt="classical figure study in the tradition of academic art, contrapposto pose, museum-quality lighting, anatomical precision, fine art anatomy study, gallery exhibition quality",
        video_prompt="slow rotation showing classical figure from multiple angles, museum gallery presentation, academic art study",
        lighting="museum directional spot, soft shadows defining musculature",
        mood="academic, classical, anatomical beauty",
    ),
    ArtisticStyle(
        name="Drapery Study",
 category="fine_art",
        image_prompt="elaborate drapery cascading over and around the figure, classical fabric study, rich textile textures catching light, Renaissance drapery mastery, wet fabric clinging to form",
        video_prompt="fabric slowly draping and settling over the figure, textile flowing and catching light, drapery animation",
        lighting="directional studio light defining fabric folds",
        mood="classical, textural, masterful",
    ),
    ArtisticStyle(
        name="Body as Landscape Aerial",
        category="fine_art",
        image_prompt="extreme close-up of curves and contours resembling aerial terrain, abstract body landscape, skin texture as geography, monochromatic fine art, museum gallery print",
        video_prompt="slow aerial-style pan across body contours, abstract terrain discovery, landscape exploration",
        lighting="soft raking light emphasizing contours and texture",
        mood="abstract, contemplative, geographic",
    ),
    ArtisticStyle(
        name="Chiaroscuro Nude Study",
        category="fine_art",
        image_prompt="Caravaggio-style chiaroscuro figure, dramatic light carving form from darkness, deep black background, single warm light source, anatomical mastery, Baroque fine art",
        video_prompt="light slowly tracing the contours of the figure, chiaroscuro revelation, Baroque dramatic lighting",
        lighting="single dramatic warm light source, deep black negative space",
        mood="dramatic, classical, powerful",
    ),
    ArtisticStyle(
        name="Marble Statue Study",
        category="fine_art",
        image_prompt="styled as classical marble sculpture, smooth stone texture, museum pedestal, dramatic gallery lighting, ancient Greek or Roman aesthetic, timeless beauty in stone",
        video_prompt="subtle animation as marble comes to life, stone texture softening to skin, classical statue awakening",
        lighting="museum spot from above, dramatic marble shadows",
        mood="timeless, sculptural, divine",
    ),
    ArtisticStyle(
        name="Ink Wash Figure",
        category="fine_art",
        image_prompt="Chinese ink wash painting style, flowing black ink on rice paper, minimalist brushwork suggesting form, negative space as composition, sumi-e figure study",
        video_prompt="ink strokes being painted in real-time, sumi-e creation animation, brush painting the figure",
        lighting="flat even light, no shadows, graphic",
        mood="minimalist, flowing, meditative",
    ),
    ArtisticStyle(
        name="Rembrandt Figure",
        category="fine_art",
        image_prompt="Rembrandt-style warm golden figure, intimate domestic interior, candlelight glow on skin, Dutch Golden Age masterwork quality, deep warm shadows",
        video_prompt="candlelight flickering on skin, Rembrandt intimate domestic scene, warm shadow movement",
        lighting="warm candlelight, golden glow, deep amber shadows",
        mood="intimate, warm, masterful",
    ),
    ArtisticStyle(
        name="Botanical Study",
        category="fine_art",
        image_prompt="figure intertwined with botanical elements, flowers growing from and around the body, Orchid and skin, nature and beauty merged, fine art botanical photography",
        video_prompt="flowers slowly blooming around the figure, botanical growth animation, nature and beauty merging",
        lighting="soft natural greenhouse light",
        mood="natural, organic, beautiful",
    ),
    ArtisticStyle(
        name="Shadow Calligraphy",
        category="fine_art",
        image_prompt="projected calligraphic shadows on skin, text and brushstrokes as light patterns, experimental body art, literary beauty, words as visual art",
        video_prompt="calligraphic shadows moving across skin, text flowing and shifting, literary light art",
        lighting="projected shadow patterns through stencil",
        mood="literary, experimental, poetic",
    ),
    ArtisticStyle(
        name="Gold Leaf Body Art",
        category="fine_art",
        image_prompt="gold leaf applied to skin, kintsugi-inspired body art, precious metal textures on human form, Japanese meets fine art, luxury meets anatomy",
        video_prompt="gold leaf being applied and catching light, precious metal shimmer on skin, luxury body art",
        lighting="warm directional light on gold textures",
        mood="luxurious, precious, artistic",
    ),
    ArtisticStyle(
        name="Mirror Hall Study",
        category="fine_art",
        image_prompt="infinite mirror reflections, multiple angles visible simultaneously, Yayoi Kusama infinity room aesthetic, multiplied beauty, kaleidoscopic fine art",
        video_prompt="infinite reflections shifting and multiplying, mirror hall animation, kaleidoscopic beauty",
        lighting="even ambient, reflections creating depth",
        mood="infinite, mesmerizing, kaleidoscopic",
    ),
    ArtisticStyle(
        name="Blue Period Study",
        category="fine_art",
        image_prompt="Picasso Blue Period style, melancholic blue tones, elongated forms, emotional figure study, somber and beautiful, monochromatic blue palette",
        video_prompt="slow melancholic movement in blue tones, Blue Period emotional atmosphere, somber beauty",
        lighting="cool blue ambient, melancholic shadows",
        mood="melancholic, emotional, blue",
    ),
    ArtisticStyle(
        name="Light Box Silhouette",
        category="fine_art",
        image_prompt="backlit light box creating perfect silhouette, edge lighting defining form, high contrast, graphic body outline, minimalist fine art",
        video_prompt="silhouette shifting and changing shape, light box animation, graphic body movement",
        lighting="strong backlight, edge-lit silhouette",
        mood="graphic, minimal, powerful",
    ),
    ArtisticStyle(
        name="Polaroid Intimacy",
        category="fine_art",
        image_prompt="Polaroid instant film aesthetic, soft focus, warm color cast, intimate casual capture, vintage instant photography, raw and authentic",
        video_prompt="Polaroid developing in real-time, instant film revealing the image, vintage photography animation",
        lighting="soft flash or natural light",
        mood="intimate, raw, authentic",
    ),
    ArtisticStyle(
        name="Smoke & Figure",
        category="fine_art",
        image_prompt="figure emerging from or dissolving into smoke, atmospheric haze, body partially obscured, mysterious and ethereal, smoke art fine art",
        video_prompt="smoke swirling and revealing the figure, atmospheric reveal, smoke art animation",
        lighting="backlit through smoke, atmospheric",
        mood="mysterious, ethereal, atmospheric",
    ),
]

# ─── BOUDOIR ───────────────────────────────────────────────────────

BOUDOIR_STYLES = [
    ArtisticStyle(
        name="Classic Boudoir",
        category="boudoir",
        image_prompt="classic boudoir photography, soft morning light through sheer curtains, silk robe draped loosely, intimate bedroom setting, warm skin tones",
        video_prompt="gentle movement in soft morning light, silk fabric shifting, intimate boudoir atmosphere, slow cinematic pan",
        lighting="soft window light, warm diffused glow",
        mood="intimate, soft, luxurious",
    ),
    ArtisticStyle(
        name="Film Noir Boudoir",
        category="boudoir",
        image_prompt="film noir style boudoir, venetian blind shadows across the body, high contrast black and white, dramatic shadows, 1940s glamour",
        video_prompt="venetian blind shadow patterns slowly moving across the subject, noir atmosphere, cigarette smoke curling",
        lighting="harsh directional light through blinds, deep shadows",
        mood="mysterious, seductive, cinematic",
    ),
    ArtisticStyle(
        name="Silk & Satin",
        category="boudoir",
        image_prompt="silk and satin fabric draped artfully, luxurious textures catching light, rich jewel tones, close-up fabric and skin detail",
        video_prompt="silk fabric slowly sliding and pooling, light catching satin textures, luxurious material animation",
        lighting="rim lighting on fabric textures, warm",
        mood="luxurious, tactile, sensual",
    ),
    ArtisticStyle(
        name="Mirror Reflection",
        category="boudoir",
        image_prompt="artistic mirror reflection portrait, multiple angles visible, vanitas theme, ornate vintage mirror frame, soft focus background",
        video_prompt="reflection shifting as subject moves, mirror world perspective, vanitas meditation",
        lighting="soft ambient light, reflections creating depth",
        mood="introspective, layered, artistic",
    ),
    ArtisticStyle(
        name="Candlelit Intimacy",
        category="boudoir",
        image_prompt="candlelit boudoir scene, warm flickering light, golden glow on skin, intimate atmosphere, multiple candles creating depth",
        video_prompt="candlelight flickering and dancing, warm shadows moving, intimate candlelit atmosphere",
        lighting="multiple candle sources, warm flickering glow",
        mood="intimate, warm, romantic",
    ),
    ArtisticStyle(
        name="Morning Light",
        category="boudoir",
        image_prompt="early morning bedroom light, soft golden rays through white curtains, tangled sheets, natural and unposed, dewy skin",
        video_prompt="morning light slowly brightening, curtains gently billowing, natural morning atmosphere",
        lighting="soft directional morning sun, warm golden",
        mood="natural, fresh, intimate",
    ),
    ArtisticStyle(
        name="Shadow Play",
        category="boudoir",
        image_prompt="artistic shadow patterns on skin, projected through lace or foliage, abstract body landscape, high contrast monochrome",
        video_prompt="shadow patterns slowly moving across skin, lace shadow animation, abstract light play",
        lighting="hard light through patterned gobo",
        mood="abstract, artistic, mysterious",
    ),
    ArtisticStyle(
        name="Velvet & Dim Light",
        category="boudoir",
        image_prompt="deep velvet backdrop, dim moody lighting, rich burgundy and black tones, luxurious textures, intimate close-up",
        video_prompt="slow intimate movement in dim velvet-lit space, rich textures catching low light",
        lighting="dim single source, warm undertones",
        mood="moody, luxurious, intimate",
    ),
    ArtisticStyle(
        name="Sheer Fabric Layers",
        category="boudoir",
        image_prompt="multiple layers of sheer fabric creating depth and mystery, translucent textures, backlit silhouette, ethereal and dreamlike",
        video_prompt="sheer fabric floating and settling, layers shifting in gentle breeze, ethereal motion",
        lighting="backlit through sheer fabric, soft diffusion",
        mood="ethereal, mysterious, delicate",
    ),
    ArtisticStyle(
        name="Window Silhouette",
        category="boudoir",
        image_prompt="silhouette against bright window, backlit figure, detail in shadow, bright blown-out background, dramatic contrast",
        video_prompt="silhouette moving against bright window, dramatic backlit motion, figure against light",
        lighting="strong backlight from window",
        mood="dramatic, anonymous, powerful",
    ),
    # ─── EXPANDED BOUDOIR STYLES ────────────────────────────────────
    ArtisticStyle(
        name="Close-Up Skin Study",
        category="boudoir",
        image_prompt="extreme close-up of skin texture, pores and fine hairs visible, macro beauty photography, intimate skin detail, warm golden tones, tactile and sensual",
        video_prompt="slow macro pan across skin surface, intimate texture exploration, tactile beauty",
        lighting="soft warm raking light on skin texture",
        mood="intimate, tactile, sensual",
    ),
    ArtisticStyle(
        name="Collarbone & Neck",
        category="boudoir",
        image_prompt="close-up of collarbone and neck, elegant elongation, soft shadow in hollow, jewelry catching light, graceful anatomical detail",
        video_prompt="gentle head tilt revealing collarbone, graceful neck movement, elegant anatomy",
        lighting="soft side light defining collarbone contours",
        mood="elegant, graceful, intimate",
    ),
    ArtisticStyle(
        name="Back Study",
        category="boudoir",
        image_prompt="elegant back study, spine as landscape, shoulder blades and musculature, classical back beauty, soft lighting on skin curves",
        video_prompt="subtle movement of back muscles, shoulder blade shift, elegant back anatomy in motion",
        lighting="soft directional light from above",
        mood="elegant, sculptural, intimate",
    ),
    ArtisticStyle(
        name="Hand & Face",
        category="boudoir",
        image_prompt="intimate close-up of hands touching face, fingers tracing features, gentle and tender gesture, soft focus background, emotional connection",
        video_prompt="fingers slowly tracing face contours, tender intimate gesture, gentle touch animation",
        lighting="soft warm ambient, gentle shadows",
        mood="tender, intimate, emotional",
    ),
    ArtisticStyle(
        name="Lace Overlay",
        category="boudoir",
        image_prompt="intricate lace fabric draped over skin, delicate pattern casting shadows, textural interplay of lace and skin, vintage elegance",
        video_prompt="lace fabric slowly settling on skin, pattern shadows shifting, delicate textile animation",
        lighting="directional light through lace creating pattern shadows",
        mood="delicate, elegant, textural",
    ),
    ArtisticStyle(
        name="Lipstick & Skin",
        category="boudoir",
        image_prompt="close-up beauty detail, glossy lip color, dewy skin, perfect makeup detail, macro beauty photography, magazine quality close-up",
        video_prompt="lip movement, subtle expression, macro beauty detail",
        lighting="beauty dish, soft even glamour",
        mood="glamorous, polished, beautiful",
    ),
    ArtisticStyle(
        name="Feather Light",
        category="boudoir",
        image_prompt="soft feather touching skin, delicate and playful, light and airy composition, white feathers on warm skin, whimsical boudoir",
        video_prompt="feather drifting and touching skin, light playful movement, whimsical boudoir animation",
        lighting="bright soft fill, airy and light",
        mood="playful, light, whimsical",
    ),
    ArtisticStyle(
        name="Rose Petal Study",
        category="boudoir",
        image_prompt="rose petals scattered on skin, red and pink botanical beauty, romantic texture, floral intimacy, natural luxury",
        video_prompt="rose petals falling and settling on skin, romantic botanical animation, floral beauty",
        lighting="soft warm ambient, romantic",
        mood="romantic, botanical, luxurious",
    ),
    ArtisticStyle(
        name="Bed Linen Texture",
        category="boudoir",
        image_prompt="tangled in white bed linens, fabric and skin interplay, morning after aesthetic, rumpled sheets and soft light, intimate domestic beauty",
        video_prompt="movement in tangled sheets, fabric shifting, intimate morning atmosphere",
        lighting="soft morning window light through curtains",
        mood="intimate, natural, comfortable",
    ),
    ArtisticStyle(
        name="Perfume & Jewelry",
        category="boudoir",
        image_prompt="close-up of perfume bottle and jewelry on skin, luxury accessories detail, sparkle and glass catching light, intimate luxury still life with figure",
        video_prompt="jewelry catching light as skin moves, luxury detail animation, sparkle and reflection",
        lighting="sparkle-catching directional light",
        mood="luxurious, precious, intimate",
    ),
    ArtisticStyle(
        name="Stocking Detail",
        category="boudoir",
        image_prompt="close-up of stocking texture on skin, fishnet or silk pattern, textural contrast, classic boudoir detail, vintage glamour",
        video_prompt="stocking texture detail, fabric pattern on skin, classic boudoir texture",
        lighting="side light emphasizing texture",
        mood="classic, textural, glamorous",
    ),
    ArtisticStyle(
        name="High Heel Elegance",
        category="boudoir",
        image_prompt="close-up of high heel shoe and ankle, elegant footwear detail, leg line composition, classic glamour accessory, stiletto beauty",
        video_prompt="ankle and foot movement in heels, elegant shoe detail, glamour accessory animation",
        lighting="directional light on shoe and leg",
        mood="glamorous, elegant, powerful",
    ),
    ArtisticStyle(
        name="Eye Close-Up",
        category="boudoir",
        image_prompt="extreme close-up of eye, iris detail, lashes, reflective gaze, soulful intimate portrait, the eyes have it, emotional connection through gaze",
        video_prompt="eye movement, blink, gaze shift, intimate eye contact",
        lighting="catch light in eye, soft ambient",
        mood="intimate, soulful, connecting",
    ),
    ArtisticStyle(
        name="Shoulder & Clavicle",
        category="boudoir",
        image_prompt="bare shoulder and clavicle detail, soft skin, elegant bone structure, intimate anatomy study, beauty in simplicity",
        video_prompt="subtle shoulder movement, graceful anatomy, intimate detail",
        lighting="soft side light on shoulder contours",
        mood="elegant, intimate, simple",
    ),
    ArtisticStyle(
        name="Draped Sheet",
        category="boudoir",
        image_prompt="white sheet draped loosely, strategic covering and revealing, classical Venus pose, timeless beauty, Greek goddess aesthetic",
        video_prompt="sheet slowly slipping and being caught, classical drapery movement, Venus reveal",
        lighting="soft studio light, classical",
        mood="timeless, classical, beautiful",
    ),
]

# ─── EDITORIAL ─────────────────────────────────────────────────────

EDITORIAL_STYLES = [
    ArtisticStyle(
        name="Vogue Italia",
        category="editorial",
        image_prompt="high fashion editorial, Vogue Italia quality, dramatic lighting, bold makeup, architectural poses, luxury designer aesthetic",
        video_prompt="confident editorial walk, dramatic fashion movement, Vogue-quality motion",
        lighting="dramatic studio lighting, sculptural shadows",
        mood="powerful, confident, high-fashion",
    ),
    ArtisticStyle(
        name="Black & White Editorial",
        category="editorial",
        image_prompt="striking black and white editorial, high contrast, grain texture, Helmut Newton inspired, powerful and provocative composition",
        video_prompt="dramatic black and white movement, grainy film texture, powerful editorial motion",
        lighting="high contrast studio, deep shadows",
        mood="powerful, timeless, provocative",
    ),
    ArtisticStyle(
        name="Harper's Bazaar Glamour",
        category="editorial",
        image_prompt="glamorous Harper's Bazaar style, soft beauty lighting, flawless skin, elegant styling, timeless beauty editorial",
        video_prompt="elegant beauty reveal, soft glamour motion, timeless editorial movement",
        lighting="soft butterfly lighting, beauty dish",
        mood="glamorous, elegant, timeless",
    ),
    ArtisticStyle(
        name="Avant-Garde Fashion",
        category="editorial",
        image_prompt="avant-garde fashion editorial, unconventional styling, geometric shapes, bold artistic choices, high fashion art",
        video_prompt="avant-garde movement and posing, unconventional fashion motion, artistic expression",
        lighting="dramatic directional, sculptural",
        mood="avant-garde, bold, artistic",
    ),
    ArtisticStyle(
        name="Minimalist Scandinavian",
        category="editorial",
        image_prompt="clean Scandinavian minimalism, neutral tones, simple styling, white space, modern and fresh, Nordic beauty",
        video_prompt="clean minimal movement, Scandinavian simplicity, fresh and modern motion",
        lighting="soft even natural light, clean",
        mood="clean, fresh, modern",
    ),
    ArtisticStyle(
        name="Vintage Film",
        category="editorial",
        image_prompt="vintage film photography look, warm color grading, film grain, light leaks, 1970s aesthetic, analog warmth",
        video_prompt="vintage film look with light leaks and grain, 70s aesthetic motion, analog warmth",
        lighting="warm natural light, golden hour tones",
        mood="nostalgic, warm, vintage",
    ),
    ArtisticStyle(
        name="Neon Noir",
        category="editorial",
        image_prompt="neon-lit editorial, cyberpunk color palette, blue and pink neon reflections, wet street reflections, futuristic glamour",
        video_prompt="neon lights reflecting and shifting, cyberpunk atmosphere, futuristic editorial motion",
        lighting="colored neon practicals, blue and pink",
        mood="futuristic, edgy, glamorous",
    ),
    ArtisticStyle(
        name="Tropical Luxe",
        category="editorial",
        image_prompt="tropical luxury editorial, palm frond shadows, warm golden light, resort glamour, lush greenery backdrop",
        video_prompt="palm fronds swaying, tropical breeze, warm resort atmosphere, luxury travel motion",
        lighting="dappled tropical sunlight through palms",
        mood="luxurious, tropical, warm",
    ),
    ArtisticStyle(
        name="Urban Edge",
        category="editorial",
        image_prompt="urban street editorial, gritty city backdrop, concrete and glass, street style fashion, dynamic poses, metropolitan energy",
        video_prompt="confident urban walk, city energy, street style motion, metropolitan editorial",
        lighting="available urban light, neon signs",
        mood="edgy, confident, urban",
    ),
    ArtisticStyle(
        name="Garden Editorial",
        category="editorial",
        image_prompt="lush garden editorial, surrounded by flowers, soft romantic lighting, editorial in nature, floral beauty",
        video_prompt="flowers swaying in breeze, garden atmosphere, romantic editorial motion",
        lighting="soft natural light through foliage",
        mood="romantic, natural, beautiful",
    ),
]

# ─── PORTRAIT ──────────────────────────────────────────────────────

PORTRAIT_STYLES = [
    ArtisticStyle(
        name="Beauty Close-Up",
        category="portrait",
        image_prompt="extreme beauty close-up, flawless skin detail, macro beauty photography, perfect makeup, magazine cover quality",
        video_prompt="slow beauty reveal, skin detail, intimate close-up motion",
        lighting="beauty dish, soft butterfly lighting",
        mood="confident, flawless, powerful",
    ),
    ArtisticStyle(
        name="Environmental Portrait",
        category="portrait",
        image_prompt="environmental portrait, subject in meaningful location, natural context telling a story, documentary style, authentic",
        video_prompt="subject interacting naturally with environment, documentary-style motion",
        lighting="natural available light",
        mood="authentic, meaningful, real",
    ),
    ArtisticStyle(
        name="Headshot Glamour",
        category="portrait",
        image_prompt="glamorous headshot, perfect lighting, warm skin tones, direct eye contact, professional beauty, magazine quality",
        video_prompt="subtle expression change, confident gaze, glamour headshot motion",
        lighting="three-point studio lighting",
        mood="confident, beautiful, professional",
    ),
    ArtisticStyle(
        name="Golden Hour Portrait",
        category="portrait",
        image_prompt="golden hour portrait, warm sunset backlight, lens flare, sun-kissed skin, outdoor beauty, magical warm tones",
        video_prompt="golden hour movement, sun flaring, warm wind-blown hair, magical outdoor portrait",
        lighting="backlit golden hour sun",
        mood="warm, magical, natural beauty",
    ),
    ArtisticStyle(
        name="Studio White Background",
        category="portrait",
        image_prompt="clean white background portrait, pure and minimal, focus entirely on the subject, commercial beauty quality",
        video_prompt="clean studio movement, white backdrop, focused portrait motion",
        lighting="even studio lighting, minimal shadows",
        mood="clean, commercial, focused",
    ),
    ArtisticStyle(
        name="Dramatic Side Light",
        category="portrait",
        image_prompt="dramatic single side light portrait, half face illuminated, half in shadow, sculptural quality, intense and powerful",
        video_prompt="light slowly revealing the face from shadow, dramatic portrait animation",
        lighting="single hard side light",
        mood="intense, powerful, dramatic",
    ),
    ArtisticStyle(
        name="Soft Natural Beauty",
        category="portrait",
        image_prompt="natural beauty portrait, minimal makeup, natural hair, soft window light, authentic and real, fresh-faced beauty",
        video_prompt="natural candid moment, soft natural light, authentic beauty motion",
        lighting="soft natural window light",
        mood="natural, fresh, authentic",
    ),
    ArtisticStyle(
        name="High-Key Glamour",
        category="portrait",
        image_prompt="high-key beauty portrait, bright and airy, minimal shadows, white and pastel tones, ethereal beauty",
        video_prompt="bright airy movement, ethereal high-key motion, soft glamour",
        lighting="bright even lighting, minimal shadows",
        mood="bright, ethereal, light",
    ),
    ArtisticStyle(
        name="Low-Key Drama",
        category="portrait",
        image_prompt="low-key dramatic portrait, deep shadows, single light source, moody and intense, film noir quality",
        video_prompt="dramatic shadow reveal, low-key intensity, noir portrait motion",
        lighting="single low-key source, deep blacks",
        mood="dramatic, moody, intense",
    ),
    ArtisticStyle(
        name="Infrared Beauty",
        category="portrait",
        image_prompt="infrared photography style, ethereal white foliage, pink and red skin tones, dreamlike infrared landscape portrait",
        video_prompt="infrared dream landscape, ethereal foliage movement, surreal beauty motion",
        lighting="infrared spectrum glow",
        mood="surreal, ethereal, otherworldly",
    ),
]

# ─── CONCEPTUAL ────────────────────────────────────────────────────

CONCEPTUAL_STYLES = [
    ArtisticStyle(
        name="Body as Landscape",
        category="conceptual",
        image_prompt="body landscape photography, abstract curves resembling mountains and valleys, aerial perspective, monochromatic, fine art abstraction",
        video_prompt="slow aerial-style pan across body as landscape, abstract terrain motion, fine art movement",
        lighting="soft directional, emphasizing contours",
        mood="abstract, contemplative, artistic",
    ),
    ArtisticStyle(
        name="Double Exposure",
        category="conceptual",
        image_prompt="double exposure portrait, face merged with nature or architecture, ethereal overlay, fine art composite, dreamlike layering",
        video_prompt="double exposure layers shifting and merging, nature and portrait blending, composite animation",
        lighting="soft ambient, multiple exposures",
        mood="dreamlike, layered, artistic",
    ),
    ArtisticStyle(
        name="Light Painting",
        category="conceptual",
        image_prompt="long exposure light painting, trails of light creating shapes around the figure, dark background, colorful light streaks, experimental",
        video_prompt="light trails being painted in real-time, long exposure animation, colorful light movement",
        lighting="light painting trails, dark environment",
        mood="experimental, dynamic, artistic",
    ),
    ArtisticStyle(
        name="Prism Refraction",
        category="conceptual",
        image_prompt="crystal prism creating rainbow refractions, spectral light splitting, colorful light leaks, experimental photography",
        video_prompt="prism rotating, rainbow refractions shifting across the subject, spectral light animation",
        lighting="prismatic refraction, rainbow spectrum",
        mood="experimental, colorful, magical",
    ),
    ArtisticStyle(
        name="Smoke & Silhouette",
        category="conceptual",
        image_prompt="smoke machine creating atmospheric haze, silhouette emerging from fog, mysterious and dramatic, studio smoke art",
        video_prompt="smoke swirling and parting to reveal the figure, atmospheric reveal, smoke art motion",
        lighting="backlit through smoke, atmospheric",
        mood="mysterious, atmospheric, dramatic",
    ),
    ArtisticStyle(
        name="Fractal Patterns",
        category="conceptual",
        image_prompt="fractal patterns projected onto the body, geometric light projections, mathematical beauty, experimental body art",
        video_prompt="fractal projections shifting and rotating on skin, geometric light animation, mathematical motion",
        lighting="projected fractal patterns",
        mood="experimental, mathematical, beautiful",
    ),
    ArtisticStyle(
        name="Underwater Ethereal",
        category="conceptual",
        image_prompt="underwater fine art photography, flowing hair and fabric, blue-green tones, weightless beauty, aquatic dreamscape",
        video_prompt="underwater movement, hair and fabric flowing weightlessly, aquatic beauty motion",
        lighting="diffused underwater light, caustic patterns",
        mood="ethereal, weightless, dreamlike",
    ),
    ArtisticStyle(
        name="Projection Mapping",
        category="conceptual",
        image_prompt="digital projection mapped onto the body, patterns and images projected on skin, technology meets beauty, contemporary art",
        video_prompt="projected images shifting and changing on the body, digital art projection animation",
        lighting="digital projection, high contrast",
        mood="futuristic, technological, artistic",
    ),
    ArtisticStyle(
        name="Frozen Motion",
        category="conceptual",
        image_prompt="high-speed freeze frame, water splash or fabric caught mid-air, crystal sharp detail, dynamic frozen moment",
        video_prompt="slow motion unfreezing, splash and movement resuming from frozen frame, high-speed beauty",
        lighting="strobe freeze, sharp detail",
        mood="dynamic, powerful, frozen energy",
    ),
    ArtisticStyle(
        name="Decay & Beauty",
        category="conceptual",
        image_prompt="beauty surrounded by decay, flowers wilting, juxtaposition of youth and aging, memento mori, philosophical beauty",
        video_prompt="flowers slowly wilting while beauty remains, passage of time, memento mori animation",
        lighting="soft natural, melancholic",
        mood="philosophical, melancholic, beautiful",
    ),
]

# ─── CINEMATIC ─────────────────────────────────────────────────────

CINEMATIC_STYLES = [
    ArtisticStyle(
        name="Wong Kar-wai Romance",
        category="cinematic",
        image_prompt="Wong Kar-wai cinematography style, saturated colors, neon reflections, rain-slicked streets, intimate longing, In the Mood for Love aesthetic",
        video_prompt="slow romantic movement through neon-lit rain, Wong Kar-wai pacing, intimate longing motion",
        lighting="neon practicals, warm saturated",
        mood="romantic, longing, atmospheric",
    ),
    ArtisticStyle(
        name="David Lynch Dream",
        category="cinematic",
        image_prompt="David Lynch surreal atmosphere, red curtains, strange shadows, uncanny beauty, Twin Peaks aesthetic, dreamlike unease",
        video_prompt="Lynchian slow reveal, red curtain movement, surreal dream motion",
        lighting="harsh contrast, uncanny shadows",
        mood="surreal, uncanny, dreamlike",
    ),
    ArtisticStyle(
        name="Blade Runner Neon",
        category="cinematic",
        image_prompt="Blade Runner 2049 aesthetic, vast neon landscapes, holographic colors, futuristic beauty, rain and neon reflections",
        video_prompt="neon lights reflecting in rain, futuristic atmosphere, Blade Runner motion",
        lighting="neon practicals, volumetric fog",
        mood="futuristic, atmospheric, lonely beauty",
    ),
    ArtisticStyle(
        name="Terrence Malick Nature",
        category="cinematic",
        image_prompt="Terrence Malick naturalistic style, golden hour magic, whispered intimacy, nature as co-star, Tree of Life aesthetic",
        video_prompt="natural movement through golden fields, Malick-style intimate moments, nature and beauty intertwined",
        lighting="magic hour natural light",
        mood="natural, intimate, spiritual",
    ),
    ArtisticStyle(
        name="Stanley Kubrick Symmetry",
        category="cinematic",
        image_prompt="Kubrick one-point perspective, perfect symmetry, centered composition, clinical precision, The Shining framing",
        video_prompt="slow dolly approach, perfect symmetrical movement, Kubrick precision",
        lighting="even clinical lighting, no shadows",
        mood="precise, unsettling, geometric",
    ),
    ArtisticStyle(
        name="Italian Neorealism",
        category="cinematic",
        image_prompt="Italian neorealist style, natural locations, documentary feel, beautiful in simplicity, Fellini-inspired, authentic European beauty",
        video_prompt="natural documentary-style movement, authentic neorealist motion, European cinema",
        lighting="natural available light",
        mood="authentic, European, real beauty",
    ),
    ArtisticStyle(
        name="Wes Anderson Pastel",
        category="cinematic",
        image_prompt="Wes Anderson pastel color palette, centered framing, quirky set design, symmetry and whimsy, vintage color grading",
        video_prompt="symmetrical Wes Anderson movement, pastel world, quirky and whimsical motion",
        lighting="flat even pastel lighting",
        mood="whimsical, quirky, pastel perfection",
    ),
    ArtisticStyle(
        name="Alfred Hitchcock Suspense",
        category="cinematic",
        image_prompt="Hitchcock thriller aesthetic, dramatic shadows, voyeuristic framing, suspenseful composition, black and white noir",
        video_prompt="Hitchcock zoom, suspenseful reveal, dramatic shadow movement",
        lighting="dramatic noir lighting",
        mood="suspenseful, dramatic, noir",
    ),
    ArtisticStyle(
        name="Sofia Coppola Dreamy",
        category="cinematic",
        image_prompt="Sofia Coppola dreamy aesthetic, soft pastel tones, Lost in Translation mood, intimate and melancholic beauty, hotel room intimacy",
        video_prompt="dreamy slow motion, intimate Sofia Coppola pacing, melancholic beauty",
        lighting="soft ambient, neon accents",
        mood="dreamy, melancholic, intimate",
    ),
    ArtisticStyle(
        name="Ridley Sci-Fi Epic",
        category="cinematic",
        image_prompt="Ridley Scott epic scale, vast landscapes, dramatic skies, alien beauty, Prometheus aesthetic, science fiction grandeur",
        video_prompt="epic landscape reveal, dramatic sky movement, sci-fi grandeur",
        lighting="dramatic natural, epic scale",
        mood="epic, vast, otherworldly",
    ),
]

# ─── NATURISTA ─────────────────────────────────────────────────────

NATURISTA_STYLES = [
    ArtisticStyle(
        name="Forest Nymph",
        category="naturista",
        image_prompt="ethereal forest setting, dappled sunlight through canopy, natural earth tones, surrounded by ferns and moss, woodland fairy",
        video_prompt="dappled forest light shifting, leaves and ferns moving, forest nymph atmosphere",
        lighting="dappled forest sunlight",
        mood="natural, ethereal, woodland",
    ),
    ArtisticStyle(
        name="Desert Goddess",
        category="naturista",
        image_prompt="vast desert landscape, golden sand dunes, dramatic sky, warm earth tones, powerful feminine presence in nature",
        video_prompt="desert wind moving sand and fabric, vast landscape, desert goddess atmosphere",
        lighting="harsh desert sun, dramatic shadows",
        mood="powerful, vast, elemental",
    ),
    ArtisticStyle(
        name="Ocean Spirit",
        category="naturista",
        image_prompt="coastal rocks and waves, sea mist, blue-green ocean tones, wind-blown hair, elemental water beauty",
        video_prompt="waves crashing, sea mist rising, wind and water movement, ocean spirit",
        lighting="overcast coastal light, blue-green tones",
        mood="elemental, powerful, natural",
    ),
    ArtisticStyle(
        name="Mountain Serenity",
        category="naturista",
        image_prompt="mountain backdrop, alpine meadow, fresh clean air feeling, crisp natural beauty, high altitude clarity",
        video_prompt="mountain wind, clouds moving, alpine atmosphere, serene natural motion",
        lighting="clear mountain light",
        mood="serene, fresh, majestic",
    ),
    ArtisticStyle(
        name="Tropical Paradise",
        category="naturista",
        image_prompt="tropical waterfall, lush greenery, warm sunlight through palms, paradise found, exotic natural beauty",
        video_prompt="waterfall flowing, tropical breeze, palm fronds swaying, paradise motion",
        lighting="dappled tropical sunlight",
        mood="tropical, lush, paradise",
    ),
    ArtisticStyle(
        name="Starry Night",
        category="naturista",
        image_prompt="milky way backdrop, starlit landscape, long exposure night sky, celestial beauty, cosmic scale",
        video_prompt="stars rotating overhead, milky way movement, cosmic night motion",
        lighting="starlight and faint ambient",
        mood="cosmic, vast, mystical",
    ),
    ArtisticStyle(
        name="Autumn Warmth",
        category="naturista",
        image_prompt="autumn forest, golden and red leaves, warm golden light, fallen leaves, seasonal beauty, harvest warmth",
        video_prompt="leaves falling, autumn wind, golden forest atmosphere, seasonal motion",
        lighting="warm golden autumn light",
        mood="warm, seasonal, golden",
    ),
    ArtisticStyle(
        name="Spring Bloom",
        category="naturista",
        image_prompt="cherry blossom or wildflower meadow, spring freshness, soft pink and green palette, new growth, renewal beauty",
        video_prompt="petals falling, spring breeze, flowers blooming, renewal motion",
        lighting="soft spring light",
        mood="fresh, renewed, blooming",
    ),
    ArtisticStyle(
        name="Rain Beauty",
        category="naturista",
        image_prompt="rain portrait, wet hair and skin, rain drops catching light, dramatic weather beauty, elemental power",
        video_prompt="rain falling, water streaming, elemental weather beauty, rain portrait motion",
        lighting="overcast, rain droplets catching light",
        mood="elemental, powerful, dramatic",
    ),
    ArtisticStyle(
        name="Snow Queen",
        category="naturista",
        image_prompt="winter snow landscape, ice and frost, cool blue-white tones, crystalline beauty, winter magic",
        video_prompt="snow falling, frost crystallizing, winter wind, snow queen atmosphere",
        lighting="cool blue winter light",
        mood="crystalline, cold, magical",
    ),
]

# ─── VIDEO-SPECIFIC MOTION STYLES ─────────────────────────────────

VIDEO_MOTION_STYLES = [
    ArtisticStyle(
        name="Slow Motion Hair Flip",
        category="video",
        image_prompt="dynamic hair movement portrait",
        video_prompt="dramatic slow motion hair flip, individual strands visible, wind machine effect, high frame rate beauty",
        lighting="backlit rim light on hair",
        mood="powerful, dynamic, dramatic",
    ),
    ArtisticStyle(
        name="Fabric Flow",
        category="video",
        image_prompt="flowing fabric portrait",
        video_prompt="silk or chiffon fabric flowing in slow motion, catching light and air, ethereal fabric dance, movement poetry",
        lighting="backlit fabric, rim light",
        mood="ethereal, flowing, poetic",
    ),
    ArtisticStyle(
        name="Walking Towards Camera",
        category="video",
        image_prompt="confident walking portrait",
        video_prompt="confident slow walk towards camera, deliberate stride, model walk, approach and connection",
        lighting="front fill with backlight rim",
        mood="confident, powerful, approaching",
    ),
    ArtisticStyle(
        name="Turning to Reveal",
        category="video",
        image_prompt="over the shoulder portrait",
        video_prompt="slow turn to camera, over-the-shoulder reveal, eye contact moment, intimate connection",
        lighting="side light transitioning to front",
        mood="intimate, revealing, connecting",
    ),
    ArtisticStyle(
        name="Wind Machine Glamour",
        category="video",
        image_prompt="wind-blown glamour portrait",
        video_prompt="professional wind machine effect, hair and fabric blowing, glamour photography in motion, editorial wind",
        lighting="beauty lighting with wind",
        mood="glamorous, powerful, editorial",
    ),
    ArtisticStyle(
        name="Water Splash",
        category="video",
        image_prompt="water splash portrait",
        video_prompt="water splash in slow motion, droplets catching light, dynamic water interaction, elemental beauty",
        lighting="strobe-lit water droplets",
        mood="dynamic, elemental, powerful",
    ),
    ArtisticStyle(
        name="Candle Blowing",
        category="video",
        image_prompt="candlelight portrait",
        video_prompt="gently blowing out a candle, warm glow fading, intimate moment, breath visible in candlelight",
        lighting="candlelight fading to ambient",
        mood="intimate, soft, magical",
    ),
    ArtisticStyle(
        name="Mirror Reveal",
        category="video",
        image_prompt="mirror reflection portrait",
        video_prompt="turning to face mirror, reflection revealing the full look, vanity and beauty, mirror world",
        lighting="soft ambient with mirror reflections",
        mood="intimate, revealing, beautiful",
    ),
    ArtisticStyle(
        name="Dance Movement",
        category="video",
        image_prompt="dance portrait",
        video_prompt="fluid dance movement, contemporary or ballet-inspired, graceful body in motion, art of movement",
        lighting="studio lighting following movement",
        mood="graceful, artistic, dynamic",
    ),
    ArtisticStyle(
        name="Underwater Float",
        category="video",
        image_prompt="underwater portrait",
        video_prompt="underwater weightless movement, hair floating, fabric drifting, aquatic dream, blue-green serenity",
        lighting="diffused underwater caustic light",
        mood="weightless, ethereal, dreamlike",
    ),
]

# ─── ALL STYLES COMBINED ──────────────────────────────────────────

ALL_STYLES: list[ArtisticStyle] = (
    FINE_ART_STYLES
    + BOUDOIR_STYLES
    + EDITORIAL_STYLES
    + PORTRAIT_STYLES
    + CONCEPTUAL_STYLES
    + CINEMATIC_STYLES
    + NATURISTA_STYLES
    + VIDEO_MOTION_STYLES
)

# Category index for quick lookup
STYLE_CATEGORIES: dict[str, list[ArtisticStyle]] = {
    "fine_art": FINE_ART_STYLES,
    "boudoir": BOUDOIR_STYLES,
    "editorial": EDITORIAL_STYLES,
    "portrait": PORTRAIT_STYLES,
    "conceptual": CONCEPTUAL_STYLES,
    "cinematic": CINEMATIC_STYLES,
    "naturista": NATURISTA_STYLES,
    "video": VIDEO_MOTION_STYLES,
}


def get_styles_by_category(category: str) -> list[ArtisticStyle]:
    """Get all styles for a category."""
    return STYLE_CATEGORIES.get(category, [])


def get_random_styles(count: int, category: str | None = None) -> list[ArtisticStyle]:
    """Get N random styles, optionally from a specific category."""
    import random
    pool = STYLE_CATEGORIES.get(category, ALL_STYLES) if category else ALL_STYLES
    return random.sample(pool, min(count, len(pool)))


def get_style_names() -> dict[str, list[str]]:
    """Get all style names grouped by category."""
    return {cat: [s.name for s in styles] for cat, styles in STYLE_CATEGORIES.items()}


def count_styles() -> int:
    """Total number of available styles."""
    return len(ALL_STYLES)
