"""Effective local opening hours for scheduling and appointment coordination."""
from django.db.models import Max, Q
from django.utils import timezone

from .models import Appointment, ClinicHours, WorkCalendar


def hours_for_day(clinic_id, day):
    exception = WorkCalendar.objects.filter(clinic_id=clinic_id, cal_date=day).first()
    if exception and exception.kind != WorkCalendar.Kind.WORKING:
        return [exception] if exception.kind == WorkCalendar.Kind.MAKEUP else []
    return list(ClinicHours.objects.filter(clinic_id=clinic_id, weekday=day.isoweekday()).order_by('opens_at'))


def covers(hours, starts_at, ends_at):
    start = timezone.localtime(starts_at)
    end = timezone.localtime(ends_at)
    return start.date() == end.date() and any(h.opens_at <= start.time() and end.time() <= h.closes_at for h in hours)


def affected_appointments(exception):
    rows = Appointment.objects.filter(slot__clinic_id=exception.clinic_id, slot__starts_at__date=exception.cal_date,
        status__in=[Appointment.Status.BOOKED, Appointment.Status.ARRIVED]).select_related(
        'slot__dentist', 'patient', 'plan').order_by('slot__starts_at', 'pk')
    if exception.kind != WorkCalendar.Kind.MAKEUP:
        return rows
    rows = rows.annotate(reserved_end=Max('slot_links__slot__ends_at', filter=Q(slot_links__is_active=True)))
    return [app for app in rows if not covers([exception], app.slot.starts_at,
        max(app.reserved_end or app.slot.ends_at, app.plan.resource_ends_at if hasattr(app, 'plan') else app.slot.ends_at))]
