package com.elvarg.game.content.combat.method.impl.specials;

import com.elvarg.game.content.combat.CombatFactory;
import com.elvarg.game.content.combat.CombatSpecial;
import com.elvarg.game.content.combat.CombatType;
import com.elvarg.game.content.combat.formula.DamageFormulas;
import com.elvarg.game.content.combat.hit.PendingHit;
import com.elvarg.game.content.combat.method.CombatMethod;
import com.elvarg.game.entity.impl.Mobile;
import com.elvarg.game.model.Animation;
import com.elvarg.game.model.Graphic;
import com.elvarg.game.model.Priority;
import com.elvarg.util.Misc;

public class VoidwakerCombatMethod extends CombatMethod {

  private static final Animation ANIMATION = new Animation(1058, Priority.HIGH);
  private static final Graphic TARGET_GFX = new Graphic(76, Priority.HIGH);

  @Override
  public CombatType type() {
    return CombatType.MAGIC;
  }

  @Override
  public PendingHit[] hits(Mobile character, Mobile target) {
    int maxHit = DamageFormulas.calculateMaxMeleeHit(character);

    int minDamage = maxHit / 2;
    int maxDamage = (int) (maxHit * 1.5);

    int finalDamage = Misc.random(minDamage, maxDamage);

    PendingHit hit = PendingHit.create(character, target, this, finalDamage, true);

    return new PendingHit[] { hit };
  }

  @Override
  public void start(Mobile character, Mobile target) {
    CombatSpecial.drain(character, CombatSpecial.VOIDWAKER.getDrainAmount());
    character.performAnimation(ANIMATION);
    target.performGraphic(TARGET_GFX);
  }

  @Override
  public int attackSpeed(Mobile character) {
    return 4;
  }
}