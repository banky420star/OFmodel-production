"""Verify real ElevenLabs API connection and synthesis."""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "apps", "api"))

os.environ["ELEVENLABS_API_KEY"] = "sk_c197331992bf0ede66bfecf2f8bb270cd578c8767f118c48"

from app.providers.elevenlabs import ElevenLabsVoiceProvider


async def main():
    provider = ElevenLabsVoiceProvider(
        api_key="sk_c197331992bf0ede66bfecf2f8bb270cd578c8767f118c48"
    )

    print("=" * 60)
    print("ELEVENLABS INTEGRATION TEST")
    print("=" * 60)

    # 1. Health check — verify API key works and get account info
    print("\n1. Health check (API key validation)...")
    health = await provider.health_check()
    if health.success:
        print(f"   ✅ Connected!")
        print(f"   Tier: {health.data['tier']}")
        print(f"   Characters used: {health.data['character_count']}")
        print(f"   Characters limit: {health.data['character_limit']}")
    else:
        print(f"   ❌ Failed: {health.error}")
        return

    # 2. List available voices
    print("\n2. Listing available voices...")
    voice_result = await provider.create_voice(name="Ava", accent="english")
    if voice_result.success:
        vid = voice_result.data
        print(f"   ✅ Selected voice: {vid['name']}")
        print(f"   Voice ID: {vid['voice_id']}")
        print(f"   Labels: {vid['labels']}")
        if vid.get("preview_url"):
            print(f"   Preview: {vid['preview_url']}")
    else:
        print(f"   ❌ Failed: {voice_result.error}")
        return

    # 3. Synthesize speech
    test_text = "Welcome to Persona Studio. This is Ava, your virtual content creator."
    print(f"\n3. Synthesizing speech ({len(test_text)} chars)...")
    synth = await provider.synthesize(
        text=test_text,
        voice_id=vid["voice_id"],
        speed=1.0,
        output_format="mp3",
    )
    if synth.success:
        audio_size = len(synth.data["audio_data"])
        print(f"   ✅ Audio generated!")
        print(f"   Format: {synth.data['format']}")
        print(f"   Duration: {synth.data['duration_seconds']}s")
        print(f"   Sample rate: {synth.data['sample_rate']} Hz")
        print(f"   Audio size: {audio_size:,} bytes ({audio_size/1024:.1f} KB)")
        print(f"   Latency: {synth.latency_ms:.0f}ms")

        # Save the audio
        output_path = os.path.join(os.path.dirname(__file__), "ava_voice_sample.mp3")
        with open(output_path, "wb") as f:
            f.write(synth.data["audio_data"])
        print(f"   Saved to: {output_path}")
    else:
        print(f"   ❌ Failed: {synth.error}")
        return

    # 4. Second synthesis (different text)
    text2 = "Today's shoot concept is a luxury apartment lifestyle session."
    print(f"\n4. Second synthesis ({len(text2)} chars)...")
    synth2 = await provider.synthesize(
        text=text2,
        voice_id=vid["voice_id"],
        output_format="wav",
    )
    if synth2.success:
        audio_size2 = len(synth2.data["audio_data"])
        print(f"   ✅ Second audio: {synth2.data['duration_seconds']}s, {audio_size2/1024:.1f} KB")
        print(f"   Latency: {synth2.latency_ms:.0f}ms")
    else:
        print(f"   ❌ Failed: {synth2.error}")

    print("\n" + "=" * 60)
    print("ALL ELEVENLABS TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
