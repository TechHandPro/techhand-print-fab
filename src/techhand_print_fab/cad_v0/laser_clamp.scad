// ORIGINAL TechHand/PRINT trainer CAD v0 — training tool only
// Clamp / set-screw retainer for cylindrical bought laser diode module.
// Fixed laser ≤5 mW class — pocket for purchased module (not a firearm barrel).
// diametral clearance 0.15 mm over module_d. Set-screw M3 from side.

/* ===== PARAMETERS ===== */
module_d = 12;             // typical diode housing OD (mm); adjustable
clear_d = 0.15;            // diametral clearance
clamp_len = 22;            // clamp length along module axis
clamp_wall = 3.0;          // radial wall thickness
slot_w = 1.2;              // split slot for clamp flex
set_screw_d = 3.2;         // M3 clearance / tap pilot
nut_trap = true;           // include M3 hex nut trap opposite set-screw
nut_af = 5.5;              // M3 nut across-flats
nut_h = 2.4;
$fn = 48;

id = module_d + clear_d;
od = id + 2 * clamp_wall;

module laser_clamp() {
    difference() {
        // Outer body — short cylindrical MODULE BAY clamp (trainer labeling in README)
        hull() {
            cylinder(h=clamp_len, d=od);
            // Flat pad for set-screw boss
            translate([od/2 - 1, -6, 0])
                cube([5, 12, clamp_len]);
        }
        // Module bore
        translate([0, 0, -0.1])
            cylinder(h=clamp_len + 0.2, d=id);
        // Split slot
        translate([-od/2 - 1, -slot_w/2, -0.1])
            cube([od + 8, slot_w, clamp_len + 0.2]);
        // Set-screw hole
        translate([0, 0, clamp_len/2])
            rotate([0, 90, 0])
                cylinder(h=od, d=set_screw_d);
        if (nut_trap) {
            // Hex nut trap on opposite side of boss
            translate([-(id/2 + clamp_wall*0.3), 0, clamp_len/2])
                rotate([0, 90, 0])
                    cylinder(h=nut_h + 0.5, d=nut_af / cos(30) + 0.3, $fn=6);
        }
    }
}

laser_clamp();
