# One Last LAN — handcrafted ground-truth script

The reference artifact for dialogue quality: a small VN written by hand (Nick red-penning /
rewriting, Claude drafting), used to reverse-engineer the generation prompts. The register bar
every generated scene is judged against. WRITING RULES at the bottom — each extracted from an
actual edit Nick made.

**Premise**: Danny and Marc, best friends, 30s. Steph — Marc's wife — is due any day (a
daughter). One last co-op night: Halo CE, LASO, the run they never finished. Marc's old gaming
room is the nursery now.

**Cast**
- **danny** — the loud one. Trash-talks, tilts at hard levels, does a fake Italian accent when
  ordering pizza (since high school). Terrified, under all of it, of losing his friend.
- **marc** — the calm one. "It's fine. We have time." When Danny gets sincere, Marc dodges
  into practical details ("don't worry about it, she's due tomorrow").

**Structure**
1. Arrival + setup (bonding / none) — LOCKED
2. Game start (bonding / none) — LOCKED
3. Pillar cleared + pizza (comedy / none) — LOCKED
4. The Library (comedy-tension / game stakes) — LOCKED — THE BRANCH: choice sets flag `tilted`
5a. Tilt path (friction / low) — LOCKED — converges to 6
5b. Level-head path (flow / none) — LOCKED — converges to 6
6. Two Betrayals → Keyes falls → the decision (character / the night's real weight) — LOCKED
7. The Maw (comedy-tension → quiet) — LOCKED — opens on a path-picked beat (the choice's
   payoff), ends at the final CHOICE
8. Endings — LOCKED — breakdown (godfather + horse head) / bottled (ends on "We have time.")

---

## Scene 1 — Arrival (LOCKED)

NARR: Danny arrives at Marc's house at 8:45PM. Steph is due any day now, so he rushes in,
xbox one in hand.

marc: Whoa whoa, calm down. We've got all night.
danny: I know, I know, but we've had all night before and yet, we've never beaten Keyes on LASO.
marc: Huh.. Fair.. That why you brought the xbox?
danny: of course. Let's get it going!!!

NARR: Danny shoulders the door open, hands full with console and controllers.

danny: Right, I forgot, baby room. I'll grab a TV, plug this in for me.
marc: There's a monitor in the closet, go grab it.

NARR: Danny opens the closet and notices labels on everything.

danny: Everything in this house has a label now. You have a label maker. When did you get a
label maker.
marc: Steph got the label maker
danny: Steph got the label maker. Okay. Found the monitor, and conveniently an HDMI cable.

NARR: They get the xbox plugged in. The game needs to install.

danny: You're kidding me.
marc: It's fine. We have time.
danny: It's nine o'clock, Marc.
marc: We have time.

NARR: The install bar crawls. Menu music. Neither of them says anything for a minute, and they
sit in a comfortable silence.

danny: LASO. No saves.
marc: There are saves. That's a setting that exists.
danny: No saves.

## Scene 2 — Game start (LOCKED)

NARR: The game boots up with its AAAAAAAHHHH... AAAAAAAAAHHHH... AAAAAHHHH...
AAAAAAAAAAAHHHHHH. The monks are back. The two of them jump in. The Pillar of Autumn. Both of
them alive, for now.

danny: Rules check. Iron's on, so if either of us dies—
marc: We both go back. I know. I was there when we first tried this in 2004
danny: In 2004 you died on the bridge. To the first grunt.
marc: That grunt got lucky.
danny: He walked up to you. He walked, Marc. You watched him do it.

NARR: They clear the first hallway. Then the second. It keeps going fine, which neither of
them trusts.

danny: We're cooking. I'm just gonna say it. Two grown men, absolutely cooking—

NARR: Danny dies to a needler he did not see. The level restarts.

marc: ...
danny: Aaaaalllright... That one's on me.
marc: Mm.
danny: Say it.
marc: I didn't say anything.
danny: Cover the left this time.

NARR: The level restarts twice more. Nobody's mad yet.

danny: Pistol. Charge the plasma, strip his shields, then I pop him. Like civilized people.
marc: Go on three. One... two—
danny: THREE.

## Scene 3 — Pillar cleared + pizza (LOCKED)

NARR: An hour in, Marc dies to an elite just before jumping into the escape pod to the ring.

danny: Come on Marc! We don't have to kill it, just don't die, we just need to get to the
escape pod.
marc: Fine fine, but I feel like the solution is actually just more dakka dakka.
danny: Sometimes more dakka dakka is all we need. This isn't one of those times though.

NARR: Dodging the elites they make it into the escape pod, just barely, but just barely is
enough to get through.

danny: HELL YEAH. ONE LEVEL DOWN!
marc: Chill out man, we've still got like 7 more. We always get fucked on the library after all.
danny: Nah man, we got this! Look how easy that was.
danny: Oh, it's early still, but we should get the pizza now, before they close. It'll take
some time to get here anyway
marc: *feigning a crappy italian accent* Are-a you gonna use-a the Italian-a accent again?
It's-a been a while since I've-a heard it.
danny: Hah, better be careful making fun of it, or I'll make sure your daughter's first words
are "make-a da pizza"
marc: Oh my god, Steph would kill me. Just order the pizza.
danny: I'll-a have you know, this-a is-a most accurate-a accenta you've ever hearda!

NARR: Marc can't contain his laughter as danny calls the pizza place with the same crappy
Italian accent he's used for years.

## Scene 4 — The Library (LOCKED; choice sets flag `tilted`)

NARR: The Library. 1:15 AM. Pizza grease on one controller. Forty minutes of Flood so far.

danny: Who made this level. I want a name. Somebody sat down and made this on purpose.
marc: Same hallway. Four times now. I'm not saying it's lazy, but it is the same hallway.

NARR: A rocket flood fires from somewhere neither of them can see. Marc dies. The level
restarts.

marc: That was not visible. I want it on record that that was not visible.
danny: Recorded. Noted. Go again.

NARR: They get further. Danny dies reloading.

danny: I was RELOADING. It got me WHILE RELOADING.
marc: Reload behind a pillar next time.
danny: There IS no pillar, Marc. It's the Library. It's a thousand miles of the same gray
hallway and none of them have a pillar—
marc: Pizza's getting cold.
danny: ...Hand me a slice.

NARR: 1:50 AM. The deepest run they've ever had. Past the elevator. Past the part with the
door. Marc steps somewhere wrong, a carrier form pops, and it's over.

NARR: The level restarts. Danny squeezes the controller. Hard.

CHOICE:
- Set it down. Breathe. "Okay. We go again."   [-> 5b]
- "FUCK. THIS. LEVEL."                          [-> 5a, set_flag tilted]

## Scene 5a — Tilt path (LOCKED)

NARR: Danny throws the controller into the couch cushions. Not hard enough to break it. Hard
enough to count.

marc: Feel better?
danny: No.

NARR: He retrieves it. They go again. They die again.

danny: I'm getting worse. I'm actively getting worse at this game.
marc: You're tired.
danny: I'm not tired. It's— what time is it. Okay. I'm tired.
marc: We have—
danny: Do NOT say we have time. Say anything else.

NARR: Marc says nothing. The next run goes eleven minutes. Then fourteen.

marc: Left.
danny: Got him.

NARR: Then the room goes quiet in the way it only does when everything is working.

NARR: The Index chamber. They take it. The level is over.

danny: ...We're through.
marc: We're through.
danny: I yelled at your couch.
marc: The couch had it coming.

## Scene 5b — Level-head path (LOCKED)

NARR: Danny sets the controller down. Breathes. Picks it back up.

danny: Okay. New rule. Nobody says we're cooking. Nobody says it's going well. Nobody says
anything.
marc: Superstition isn't—
danny: NOTHING.

NARR: The next run goes long. Nobody says anything except the calls.

marc: Left.
danny: Got him.
marc: Rockets.
danny: Seen.

NARR: The Index chamber.

danny: Don't say anything. Don't even breathe—

NARR: They take the Index. The level is over.

danny: HELL YEAH!! THE LIBRARY! THE LIBRARY, MARC!
marc: TWENTY YEARS!

NARR: Marc remembers the neighbors halfway through the second yell. Danny does not.

## Scene 6 — Keyes falls; the decision (LOCKED except the Keyes reply)

NARR: Two Betrayals falls at 3:40. Danny flies the banshee like it owes him money.

NARR: 4:25 AM. Keyes.

danny: Twenty years.
marc: 20 years of not enough dakka.

NARR: Keyes falls in forty minutes. No deaths. Neither of them can explain it.

danny: That's IT? That's the level? THAT'S the level that's been whooping us since high school?
marc: Apparently.
danny: I don't know how to feel about this.

NARR: 5:10 AM. The menu shows the last level. The Maw. Marc looks at the clock.

danny: We just have one more level.
marc: Steph could pop any day, man. I need to be able to drive to a hospital.
danny: I know but... we'll probably never do this again...

NARR: Marc looks at the clock again. Then at the screen. Then at neither.

marc: ...The bag's in the car anyway. If she pops, we pause.
danny: If she pops, we pause.

## Scene 7 — The Maw (DRAFT)

NARR: The Maw. 5:40 AM.

[IF tilted]
danny: Hey. About the couch thing.
marc: The couch forgives you.
danny: It wasn't about the level.
marc: ...I know. Hog's spawning.
[ELSE]
danny: Is that the sun? Apollo's out here doing his job, Marc. WHY AREN'T YOU?
marc: I'm literally carrying you right now.
[END IF]

danny: Okay. The run. Six minutes.
marc: Don't flip the hog.
danny: I have never once—
marc: Don't. Flip. The hog.

NARR: He flips the hog.

danny: NOT A WORD.

NARR: They right it. The timer is unkind.

danny: GO GO GO GO
marc: HOLD ON—

NARR: The Longsword. The timer stops. And then the credits are rolling, and it's over.

marc: ...We beat it.
danny: We beat it.

CHOICE:
- "Tell Marc what's been eating you all night"   [-> ending: breakdown]
- "Keep it light — let him sleep"                [-> ending: bottled]

## Ending A — Breakdown (LOCKED)

NARR: 6:05 AM. The credits end. Nobody moves to skip them.

danny: Marc. I gotta say something.
marc: Okay.
danny: Tonight was— I keep starting this wrong.
marc: Take your time. It's only six AM.
danny: When she comes, everything changes. And that's fine. That's the job. But this — you and
me, dumb nights like this — I've been scared I'm losing it. All night. That's why I've been
weird.
marc: You have been weird.
danny: I know.
marc: ...We were gonna do this at the hospital. Steph wanted a whole thing. So when she asks:
you were surprised.
danny: What?
marc: You're the godfather, you idiot. You're not losing anything.

NARR: Danny doesn't say anything for a while. The credits music loops.

danny: ...The godfather, you say? She's-a gonna knowa the accent.
marc: She learns that accent, you're getting a horse head in your bed.
danny: I make-a no promises.

## Ending B — Bottled (LOCKED)

danny: Welp. I should let you sleep.
marc: Yeah.
danny: Text me when it happens?
marc: You're like fourth on the list.
danny: FOURTH?
marc: Both grandmas, then the group chat.
danny: The GROUP chat?
marc: Third. You're third.
danny: I'll take it.

NARR: They pack up. Danny wraps the cords wrong. Marc rewraps them.

marc: Hey. When things settle. Halo 2. Same rules.
danny: Halo 2 LASO? That's— people have DIED, Marc. That's years of our lives.
marc: We have time.

---

## WRITING RULES (each extracted from a real edit)

1. **Open on motion, never tableau.** An arrival with a clock beats a room description.
   Premise enters as fact ("Steph is due any day now"), never as mood.
2. **First line reacts to physical behavior** ("Whoa whoa, calm down" ← Danny is rushing).
3. **Goal early, in dialogue, with proper nouns** ("we've never beaten Keyes on LASO").
   A closing button then works as ESCALATION ("LASO. No saves.") instead of a reveal.
4. **One prop chain per scene, mined deep** (closet → labels → label maker → Steph). Prop
   density is decoration — the prop version of significance injection.
5. **NARR is someone doing something, physically motivated** ("shoulders the door open, hands
   full"). Never scenery — the VN background already shows the room. NARR also carries elapsed
   time ("An hour in...", "1:50 AM.").
6. **The register that survives edits: deadpan repeats.** "Steph got the label maker. Okay." /
   "We have time." twice / "No saves." twice / "If she pops, we pause." echoed.
7. **Winks are legal when funny** ("conveniently an HDMI cable", "The monks are back") — but
   budgeted; when a note's been hit, don't rehash it ("the music does the thing it does" died
   for this). Humor buys freedom that solemnity has to pay for.
8. **Details survive why/physics/economics.** 2004 not 2013 (Halo CE is 2001; they're 30+).
   "Daughter", not "baby", once the sex is known — that's how a parent-to-be talks. A due date
   is fluid ("could pop any day"), not a deadline.
9. **Most lines are mundane; not every scene serves the plot.** Trying to be important is the
   core slop signature — at line, scene, and premise scale (a hook is not a premise; a quirk is
   not a character; tension is not scene purpose).
10. **Character notes are behavior, not abstractions.** "Deflects sincerity with logistics" is
    spec slop; "dodges into practical details" + an example line is a card.
11. **The bit belongs to the relationship, not the character.** Marc invites the accent (badly
    doing it himself); Marc redeploys "dakka dakka". A quirk one character performs alone is a
    tag; a bit both characters play is a friendship.
12. **Heavy things enter through jokes first** (the baby's first mention is a threat-gag about
    her first words). The later sincere scene lands because the topic arrived light.
13. **If the dialogue shows it, narration doesn't say it** (Danny's whisper line made the
    whisper-NARR redundant; cut).
14. **Anger is physical and object-sized** (thrown controller: "Not hard enough to break it.
    Hard enough to count.").
15. **A choice must pay off later, even small** — a branch that only changes the corridor is
    not a choice. The `tilted` flag buys scene 7's opening beat (the fear half-spoken early)
    and re-colors the endings.

16. **Objects are tools, not symbols.** A fired Chekhov gun stays fired — don't reload props
    with meaning (the xbox-stays/xbox-in-trunk ending mirror died for this). If a mentioned
    thing earns a payoff, pay it as a JOKE or an action (sun → the Apollo rib), not a metaphor.
17. **A catchphrase earns one final-beat deployment** ("We have time." as the bottled ending's
    last line — the whole unsaid promise in three established words).
18. **Period-true pop-culture quotes are friendship register** ("get in loser, we're going to
    the longsword" — men who met in 2004 quote Mean Girls; the godfather reveal MUST collide
    with the Italian accent bit because the word itself is the movie).
