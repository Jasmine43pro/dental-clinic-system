from django.http import JsonResponse
from django.urls import path
from django.contrib.auth import views as auth
from domain import patient_api, views

def health(request):
    from django.db import connection
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
    except Exception:
        return JsonResponse({'status':'unavailable'},status=503)
    return JsonResponse({'status':'ok','stage':'business-system'})

urlpatterns=[path('health/',health),path('',views.dashboard,name='dashboard'),
 path('api/patient/clinics/',patient_api.public_clinics,name='public_clinics'),
 path('api/patient/login/',patient_api.patient_login,name='patient_api_login'),
 path('api/patient/register/',patient_api.patient_register,name='patient_api_register'),
 path('api/patient/slots/',patient_api.available_slots,name='patient_api_slots'),
 path('api/patient/appointments/',patient_api.patient_appointments,name='patient_api_appointments'),
 path('api/patient/appointments/<uuid:pk>/cancel/',patient_api.cancel_appointment,name='patient_api_cancel'),
 path('api/patient/appointments/<uuid:pk>/visit/',patient_api.patient_visit,name='patient_api_visit'),
 path('api/patient/invoices/<uuid:pk>/',patient_api.patient_invoice,name='patient_api_invoice'),
 path('login/',auth.LoginView.as_view(template_name='registration/login.html'),name='login'),
 path('logout/',auth.LogoutView.as_view(),name='logout'),path('register/',views.register,name='register'),
 path('password/',auth.PasswordChangeView.as_view(template_name='registration/password.html',success_url='/'),name='password'),
 path('appointments/',views.appointment_list,name='appointments'),path('book/',views.booking,name='booking'),
 path('appointments/<uuid:pk>/',views.appointment_detail,name='appointment'),
 path('appointments/<uuid:pk>/reschedule/',views.booking,name='reschedule'),
 path('appointments/<uuid:pk>/cross-clinic/',views.cross_clinic_reschedule,name='cross_clinic_reschedule'),
 path('appointments/<uuid:pk>/<str:action>/',views.appointment_command,name='appointment_command'),
 path('visits/<uuid:pk>/materials/issue/',views.material_issue,name='material_issue'),
 path('visits/<uuid:pk>/materials/<uuid:issue_pk>/return/',views.material_return,name='material_return'),
 path('visits/<uuid:pk>/',views.visit_detail,name='visit'),path('visits/<uuid:pk>/<str:action>/',views.visit_command,name='visit_command'),
 path('invoices/<uuid:pk>/',views.invoice_detail,name='invoice'),path('invoices/<uuid:pk>/<str:action>/',views.invoice_command,name='invoice_command'),
 path('patients/',views.patients,name='patients'),path('patients/new/',views.patient_edit,name='patient_new'),
 path('patients/<uuid:pk>/timeline/',views.patient_timeline,name='patient_timeline'),
 path('patients/<uuid:pk>/identity/<str:action>/',views.patient_identity,name='patient_identity'),
 path('day-schedule/',views.day_schedule,name='day_schedule'),
 path('resources/',views.resource_coordination,name='resources'),
 path('patients/<uuid:pk>/',views.patient_edit,name='patient_edit'),path('patients/<uuid:pk>/<str:kind>/',views.patient_related,name='patient_related'),
 path('patients/<uuid:pk>/<str:kind>/<uuid:related_pk>/',views.patient_related,name='patient_related_edit'),
 path('schedules/',views.schedules,name='schedules'),path('amendments/',views.amendments,name='amendments'),
 path('amendments/<uuid:pk>/',views.amendment_review,name='amendment_review'),path('costs/',views.costs,name='costs'),
 path('reports/',views.reports,name='reports'),path('accounts/',views.accounts,name='accounts'),
 path('accounts/<uuid:pk>/toggle/',views.account_toggle,name='account_toggle'),path('roles/',views.assignments,name='assignments'),
 path('roles/<uuid:pk>/revoke/',views.assignment_revoke,name='assignment_revoke'),path('audit/',views.audit_log,name='audit'),
 path('master/<str:kind>/',views.master,name='master'),path('master/<str:kind>/<uuid:pk>/',views.master,name='master_edit'),
 path('doctor-account/',views.doctor_account,name='doctor_account'),
]
