// ORIGINAL TechHand/PRINT trainer CAD v0 — training tool only
// Trigger lever — presses a momentary switch. Pivot hole for M3.
// Blade finger pad ~15–20 mm; travel ~3–5 mm to switch. NOT a firearm trigger geometry copy.

/* ===== PARAMETERS ===== */
blade_w = 7.5;             // thickness into grip (mm)
blade_h = 18;              // finger pad height (mm)
blade_len = 16;            // forward finger pad length (mm)
arm_len = 22;              // lever arm from pivot to switch pad (mm)
arm_t = 4.0;               // arm thickness (mm)
pivot_d = 3.2;             // M3 clearance
pivot_boss_od = 8.0;
switch_pad = [8, 6, 3];    // pad that presses momentary switch
travel_hint = 4;           // design travel ~3–5 mm (document)
$fn = 48;

module trigger_lever() {
    difference() {
        union() {
            // Finger blade / pad — rounded trainer blade
            hull() {
                translate([0, 0, 0])
                    cube([blade_len * 0.3, blade_w, blade_h]);
                translate([blade_len - 3, blade_w/2, 3])
                    sphere(r=3);
                translate([blade_len - 3, blade_w/2, blade_h - 3])
                    sphere(r=3);
            }
            // Lever arm rearward to switch
            translate([-arm_len, (blade_w - arm_t)/2, blade_h/2 - arm_t/2])
                cube([arm_len + 2, arm_t, arm_t]);
            // Pivot boss
            translate([-2, blade_w/2, blade_h/2])
                rotate([90, 0, 0])
                    cylinder(h=blade_w, d=pivot_boss_od, center=true);
            // Switch press pad at end of arm
            translate([-arm_len - switch_pad[0]/2 + 1, (blade_w - switch_pad[1])/2, blade_h/2 - switch_pad[2]/2])
                cube(switch_pad);
        }
        // Pivot hole M3
        translate([-2, blade_w/2, blade_h/2])
            rotate([90, 0, 0])
                cylinder(h=blade_w + 2, d=pivot_d, center=true);
        // Finger groove comfort
        translate([blade_len * 0.45, -0.1, blade_h/2])
            rotate([-90, 0, 0])
                cylinder(h=blade_w + 0.2, d=6);
    }
}

trigger_lever();
